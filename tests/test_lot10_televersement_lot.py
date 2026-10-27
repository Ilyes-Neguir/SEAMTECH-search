"""Lot 10 — téléversement d'un grand dossier par envois successifs.

Le service est testé sans PostgreSQL (stockage manifeste + fichiers), puis
l'API complète avec un client FastAPI local. Toutes les données sont
synthétiques ; aucun document réel n'est lu.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from seamtech_search.fiches import televersement_lot as televersement
from seamtech_search.fiches.gabarits import GABARITS_EMBARQUES
from seamtech_search.retention import prune_staged_uploads


def _config(tmp_path: Path) -> Any:
    return SimpleNamespace(
        database_path=tmp_path / "data" / "search.db",
        max_file_size_bytes=1024 * 1024,
        min_free_bytes=0,
    )


def _session(config: Any, fichiers: dict[str, bytes]) -> dict[str, Any]:
    return televersement.creer_session_televersement_lot(
        config,
        nom_racine="VOILES-DEMO",
        nombre_fichiers=len(fichiers),
        taille_totale_octets=sum(len(contenu) for contenu in fichiers.values()),
    )


class TestServiceTeleversement:
    def test_envois_successifs_reprise_et_finalisation(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        fichiers = {
            "2031/COMMANDE-A/fiche_ffab.pdf": b"%PDF-synthetique-A",
            "2031/COMMANDE-A/notes.txt": "notes démo".encode(),
            "COMMANDE-B/sous-dossier/plan.dxf": b"DXF-DEMO",
        }
        session = _session(config, fichiers)
        token = session["token"]

        premier = dict(list(fichiers.items())[:2])
        recu = televersement.recevoir_fichiers_televersement_lot(
            config,
            token,
            chemins_relatives=list(premier),
            flux=[BytesIO(contenu) for contenu in premier.values()],
        )
        assert recu["nombre_fichiers_recus"] == 2
        assert recu["fichiers_acceptes"] == 2

        # Réessayer exactement le même envoi (réponse perdue) est sans effet.
        rejoue = televersement.recevoir_fichiers_televersement_lot(
            config,
            token,
            chemins_relatives=list(premier),
            flux=[BytesIO(contenu) for contenu in premier.values()],
        )
        assert rejoue["nombre_fichiers_recus"] == 2
        assert rejoue["fichiers_acceptes"] == 0
        assert rejoue["fichiers_deja_recus"] == 2

        dernier_nom, dernier_contenu = list(fichiers.items())[-1]
        televersement.recevoir_fichiers_televersement_lot(
            config,
            token,
            chemins_relatives=[dernier_nom],
            flux=[BytesIO(dernier_contenu)],
        )
        terminee = televersement.terminer_session_televersement_lot(config, token)
        assert terminee["statut"] == "pret"
        assert terminee["progression_pct"] == 100.0
        # La finalisation elle-même est idempotente.
        assert televersement.terminer_session_televersement_lot(config, token)["statut"] == "pret"

        racine = televersement.racine_session_televersement_lot(config, token)
        for relatif, contenu in fichiers.items():
            assert (racine / relatif).read_bytes() == contenu

    def test_envoi_qui_depasse_l_annonce_est_arrete_tot(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        session = televersement.creer_session_televersement_lot(
            config,
            nom_racine="VOILES-DEMO",
            nombre_fichiers=1,
            taille_totale_octets=3,
        )
        flux = BytesIO(b"x" * (1024 * 1024 + 1))

        with pytest.raises(televersement.TeleversementLotTropGrand):
            televersement.recevoir_fichiers_televersement_lot(
                config,
                session["token"],
                chemins_relatives=["trop-grand.bin"],
                flux=[flux],
            )

        assert flux.tell() == 1024 * 1024, "le flux ne doit pas être consommé entièrement"
        assert televersement.etat_session_televersement_lot(config, session["token"])["nombre_fichiers_recus"] == 0
        temporaires = (
            tmp_path / "data" / "uploads" / "lots-televerses" / session["token"] / ".tmp"
        )
        assert list(temporaires.glob("*.part")) == []

    def test_envois_concurrents_ne_perdent_aucun_fichier(self, tmp_path: Path) -> None:
        from concurrent.futures import ThreadPoolExecutor

        config = _config(tmp_path)
        fichiers = {f"affaires/fichier-{numero:02d}.txt": b"x" * 8 for numero in range(40)}
        session = _session(config, fichiers)
        blocs = [list(fichiers.items())[debut : debut + 10] for debut in range(0, 40, 10)]

        def envoyer(bloc: list[tuple[str, bytes]]) -> None:
            televersement.recevoir_fichiers_televersement_lot(
                config,
                session["token"],
                chemins_relatives=[nom for nom, _contenu in bloc],
                flux=[BytesIO(contenu) for _nom, contenu in bloc],
            )

        with ThreadPoolExecutor(max_workers=4) as executeur:
            list(executeur.map(envoyer, blocs))

        etat = televersement.terminer_session_televersement_lot(config, session["token"])
        assert etat["nombre_fichiers_recus"] == 40

    def test_normalisation_unicode_evite_les_collisions(self, tmp_path: Path) -> None:
        import unicodedata

        config = _config(tmp_path)
        nom_nfc = unicodedata.normalize("NFC", "café.txt")
        nom_nfd = unicodedata.normalize("NFD", "café.txt")
        session = televersement.creer_session_televersement_lot(
            config,
            nom_racine="VOILES-DEMO",
            nombre_fichiers=2,
            taille_totale_octets=len(b"un") + len(b"contenu different"),
        )
        televersement.recevoir_fichiers_televersement_lot(
            config,
            session["token"],
            chemins_relatives=[nom_nfd],
            flux=[BytesIO(b"un")],
        )

        with pytest.raises(televersement.TeleversementLotConflit):
            televersement.recevoir_fichiers_televersement_lot(
                config,
                session["token"],
                chemins_relatives=[nom_nfc],
                flux=[BytesIO(b"contenu different")],
            )

    @pytest.mark.parametrize(
        "relatif",
        [
            "../hors-session.txt",
            "/absolu.txt",
            "C:\\windows.txt",
            "dossier/.cache/f.txt",
            "",
        ],
    )
    def test_chemin_dangereux_refuse_avant_ecriture(self, tmp_path: Path, relatif: str) -> None:
        config = _config(tmp_path)
        session = _session(config, {"ok.txt": b"ok"})

        with pytest.raises(televersement.TeleversementLotErreur):
            televersement.recevoir_fichiers_televersement_lot(
                config,
                session["token"],
                chemins_relatives=[relatif],
                flux=[BytesIO(b"contenu")],
            )

        etat = televersement.etat_session_televersement_lot(config, session["token"])
        assert etat["nombre_fichiers_recus"] == 0
        assert not (tmp_path / "hors-session.txt").exists()

    def test_chemin_trop_profond_refuse(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        contenu = b"contenu"
        session = televersement.creer_session_televersement_lot(
            config,
            nom_racine="VOILES-DEMO",
            nombre_fichiers=1,
            taille_totale_octets=len(contenu),
        )
        with pytest.raises(televersement.TeleversementLotErreur, match="[Pp]rofondeur"):
            televersement.recevoir_fichiers_televersement_lot(
                config,
                session["token"],
                chemins_relatives=["/".join(["a"] * 33 + ["fichier.txt"])],
                flux=[BytesIO(contenu)],
            )

    def test_conflits_de_casse_et_de_contenu(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        session = _session(config, {"Dossier/Fiche.pdf": b"un", "autre.txt": b"deux"})
        token = session["token"]
        televersement.recevoir_fichiers_televersement_lot(
            config,
            token,
            chemins_relatives=["Dossier/Fiche.pdf"],
            flux=[BytesIO(b"un")],
        )

        with pytest.raises(televersement.TeleversementLotConflit):
            televersement.recevoir_fichiers_televersement_lot(
                config,
                token,
                chemins_relatives=["dossier/fiche.pdf"],
                flux=[BytesIO(b"un")],
            )
        with pytest.raises(televersement.TeleversementLotConflit):
            televersement.recevoir_fichiers_televersement_lot(
                config,
                token,
                chemins_relatives=["Dossier/Fiche.pdf"],
                flux=[BytesIO(b"contenu different")],
            )

    def test_finalisation_incomplete_puis_annulation(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        session = _session(config, {"a.txt": b"a", "b.txt": b"b"})
        token = session["token"]
        televersement.recevoir_fichiers_televersement_lot(
            config,
            token,
            chemins_relatives=["a.txt"],
            flux=[BytesIO(b"a")],
        )

        with pytest.raises(televersement.TeleversementLotConflit, match="incomplet"):
            televersement.terminer_session_televersement_lot(config, token)

        annulee = televersement.annuler_session_televersement_lot(config, token)
        assert annulee["statut"] == "annule"
        with pytest.raises(televersement.TeleversementLotIntrouvable):
            televersement.etat_session_televersement_lot(config, token)

    def test_aucun_plafond_global_de_500_fichiers(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        fichiers = {f"lot/fichier-{numero:04d}.txt": b"x" for numero in range(601)}
        session = _session(config, fichiers)
        token = session["token"]
        items = list(fichiers.items())
        for debut in range(0, len(items), 100):
            bloc = items[debut : debut + 100]
            televersement.recevoir_fichiers_televersement_lot(
                config,
                token,
                chemins_relatives=[nom for nom, _contenu in bloc],
                flux=[BytesIO(contenu) for _nom, contenu in bloc],
            )

        etat = televersement.terminer_session_televersement_lot(config, token)
        assert etat["nombre_fichiers_recus"] == 601
        racine = televersement.racine_session_televersement_lot(config, token)
        assert len(list((racine / "lot").glob("*.txt"))) == 601


class TestProtectionRetentionEtGabarits:
    def test_lots_televerses_jamais_elagues_par_age(self, tmp_path: Path) -> None:
        staging = tmp_path / "uploads"
        conserve = staging / "lots-televerses" / "jeton-demo" / "fichiers"
        conserve.mkdir(parents=True)
        (conserve / "fiche.pdf").write_bytes(b"%PDF-synthetique")
        ancien = staging / "ancien-envoi"
        ancien.mkdir()
        (ancien / "temporaire.txt").write_text("démo", encoding="utf-8")
        vieux = time.time() - 40 * 86400
        for chemin in (staging / "lots-televerses", ancien):
            os.utime(chemin, (vieux, vieux))

        supprimes = prune_staged_uploads(staging, 7)

        assert supprimes == 1
        assert (conserve / "fiche.pdf").exists()
        assert not ancien.exists()

    def test_synchronisation_gabarits_strictement_additive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import seamtech_search.fiches.gabarits as gabarits_module

        existants = [
            (gabarit.code, gabarit.version)
            for gabarit in GABARITS_EMBARQUES
            if gabarit.code != "FICHE_GV_FFAB_V1"
        ]

        class Curseur:
            def __enter__(self) -> "Curseur":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def execute(self, _sql: str, _params: object = None) -> None:
                return None

            def fetchall(self) -> list[tuple[str, int]]:
                return existants

        class Connexion:
            def __enter__(self) -> "Connexion":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def cursor(self) -> Curseur:
                return Curseur()

        class IndexFactice:
            def connect(self) -> Connexion:
                return Connexion()

        enregistres: list[tuple[str, int]] = []
        monkeypatch.setattr(
            gabarits_module,
            "enregistrer_gabarit",
            lambda _index, gabarit: enregistres.append((gabarit.code, gabarit.version)),
        )

        ajoutes = gabarits_module.synchroniser_gabarits_embarques(IndexFactice())

        assert ajoutes == ["FICHE_GV_FFAB_V1 v1"]
        assert enregistres == [("FICHE_GV_FFAB_V1", 1)]


def _client_api(tmp_path: Path) -> tuple[TestClient, Any]:
    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    racine = tmp_path / "archive"
    racine.mkdir(parents=True, exist_ok=True)
    config = AppConfig(
        root_paths=[racine],
        database_path=tmp_path / "data" / "search.db",
        database_url=None,
        storage_backend="local",
        redis_url=None,
        min_free_bytes=0,
    )
    return TestClient(create_app(config)), config


class TestApiTeleversement:
    def test_parcours_http_complet(self, tmp_path: Path) -> None:
        client, _config_api = _client_api(tmp_path)
        fichiers = {
            "COMMANDE-A/fiche_ffab.pdf": b"%PDF-synthetique",
            "COMMANDE-B/notes.txt": b"notes",
        }
        creation = client.post(
            "/imports/dossier/televersements",
            json={
                "nom_racine": "VOILES-DEMO",
                "nombre_fichiers": len(fichiers),
                "taille_totale_octets": sum(len(v) for v in fichiers.values()),
            },
        )
        assert creation.status_code == 201, creation.text
        session = creation.json()
        token = session["token"]
        assert session["limites"]["fichiers_par_envoi_recommande"] >= 1

        envoi = client.post(
            f"/imports/dossier/televersements/{token}/fichiers",
            data={"chemins": json.dumps(list(fichiers))},
            files=[
                ("fichiers", (Path(nom).name, BytesIO(contenu), "application/octet-stream"))
                for nom, contenu in fichiers.items()
            ],
        )
        assert envoi.status_code == 200, envoi.text
        assert envoi.json()["nombre_fichiers_recus"] == 2

        fin = client.post(f"/imports/dossier/televersements/{token}/terminer")
        assert fin.status_code == 200, fin.text
        assert fin.json()["statut"] == "pret"
        assert "chemin_racine" not in fin.json(), "le chemin serveur ne doit pas être exposé au navigateur"

        etat = client.get(f"/imports/dossier/televersements/{token}")
        assert etat.status_code == 200
        assert etat.json()["nombre_fichiers_recus"] == 2

    def test_http_refus_explicites(self, tmp_path: Path) -> None:
        client, _config_api = _client_api(tmp_path)
        creation = client.post(
            "/imports/dossier/televersements",
            json={"nom_racine": "DEMO", "nombre_fichiers": 1, "taille_totale_octets": 3},
        )
        token = creation.json()["token"]

        mauvais = client.post(
            f"/imports/dossier/televersements/{token}/fichiers",
            data={"chemins": json.dumps(["../secret.txt"])},
            files=[("fichiers", ("secret.txt", BytesIO(b"abc"), "text/plain"))],
        )
        assert mauvais.status_code == 422

        inconnu = client.get("/imports/dossier/televersements/jeton-inexistant-1234567890")
        assert inconnu.status_code == 404

        annulation = client.delete(f"/imports/dossier/televersements/{token}")
        assert annulation.status_code == 200
        assert client.get(f"/imports/dossier/televersements/{token}").status_code == 404

    def test_empreintes_du_manifeste_sont_celles_des_octets(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        session = _session(config, {"a/b.txt": b"abc"})
        televersement.recevoir_fichiers_televersement_lot(
            config,
            session["token"],
            chemins_relatives=["a/b.txt"],
            flux=[BytesIO(b"abc")],
        )
        racine = televersement.terminer_session_televersement_lot(config, session["token"])
        assert racine["nombre_fichiers_recus"] == 1
        session_dir = televersement.racine_session_televersement_lot(config, session["token"]).parent
        manifeste = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
        assert manifeste["fichiers"]["a/b.txt"]["empreinte_sha256"] == hashlib.sha256(b"abc").hexdigest()
