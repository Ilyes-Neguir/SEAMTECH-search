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

    def test_creation_lot_atomique_annulation_attend_et_rejeu_sans_doublon(self, tmp_path: Path) -> None:
        import threading

        config = _config(tmp_path)
        contenu = b"%PDF-synthetique"
        session = _session(config, {"AFFAIRE/fiche.pdf": contenu})
        token = session["token"]
        televersement.recevoir_fichiers_televersement_lot(
            config,
            token,
            chemins_relatives=["AFFAIRE/fiche.pdf"],
            flux=[BytesIO(contenu)],
        )
        televersement.terminer_session_televersement_lot(config, token)

        creation_commencee = threading.Event()
        liberer_creation = threading.Event()
        annulation_terminee = threading.Event()
        appels_creation: list[Path] = []
        resultats: dict[str, Any] = {}
        erreurs: dict[str, BaseException] = {}

        def creer_lot_factice(racine: Path) -> int:
            appels_creation.append(racine)
            creation_commencee.set()
            assert liberer_creation.wait(timeout=10)
            return 4242

        def lancer_creation(nom: str) -> None:
            try:
                resultats[nom] = televersement.creer_lot_session_televersement(
                    config,
                    token,
                    creer_lot=creer_lot_factice,
                )
            except BaseException as erreur:  # le test vérifie ensuite le résultat exact
                erreurs[nom] = erreur

        def lancer_annulation() -> None:
            try:
                resultats["annulation"] = televersement.annuler_session_televersement_lot(config, token)
            except BaseException as erreur:  # le test vérifie ensuite le résultat exact
                erreurs["annulation"] = erreur
            finally:
                annulation_terminee.set()

        premier = threading.Thread(target=lancer_creation, args=("premier",), daemon=True)
        annulation = threading.Thread(target=lancer_annulation, daemon=True)
        second = threading.Thread(target=lancer_creation, args=("second",), daemon=True)
        premier.start()
        try:
            assert creation_commencee.wait(timeout=10)
            annulation.start()
            second.start()
            assert not annulation_terminee.wait(timeout=0.25), (
                "l'annulation doit attendre la fin atomique de la création du lot"
            )
        finally:
            liberer_creation.set()
        premier.join(timeout=10)
        annulation.join(timeout=10)
        second.join(timeout=10)

        assert not premier.is_alive() and not annulation.is_alive() and not second.is_alive()
        assert erreurs.keys() == {"annulation"}
        assert isinstance(erreurs["annulation"], televersement.TeleversementLotConflit)
        assert len(appels_creation) == 1
        assert resultats["premier"]["id_lot"] == 4242
        assert resultats["premier"]["lot_cree"] is True
        assert resultats["second"]["id_lot"] == 4242
        assert resultats["second"]["lot_cree"] is False
        racine = resultats["premier"]["racine"]
        assert resultats["second"]["racine"] == racine
        assert (racine / "AFFAIRE/fiche.pdf").read_bytes() == contenu

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

        assert supprimes == 0
        assert (conserve / "fiche.pdf").exists()
        assert (ancien / "temporaire.txt").exists()

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


@pytest.mark.parametrize('apres_finalisation', [False, True])
@pytest.mark.parametrize('modifie', [b'BAD', b'PLUS LONG'])
def test_integrite_fichier_recue_avant_utilisation(tmp_path: Path, apres_finalisation: bool, modifie: bytes) -> None:
    config = _config(tmp_path)
    token = _session(config, {'piece.pdf': b'BON'})['token']
    televersement.recevoir_fichiers_televersement_lot(
        config, token, chemins_relatives=['piece.pdf'], flux=[BytesIO(b'BON')]
    )
    if apres_finalisation:
        televersement.terminer_session_televersement_lot(config, token)
    fichier = config.database_path.parent / 'uploads' / 'lots-televerses' / token / 'fichiers' / 'piece.pdf'
    fichier.write_bytes(modifie)
    with pytest.raises(televersement.TeleversementLotConflit, match='intégrité'):
        if apres_finalisation:
            televersement.racine_session_televersement_lot(config, token)
        else:
            televersement.terminer_session_televersement_lot(config, token)
    # Ne pas « accepter » le contenu altéré comme nouvelle vérité.
    manifest = json.loads((fichier.parent.parent / 'session.json').read_text())
    assert manifest['fichiers']['piece.pdf']['empreinte_sha256'] == hashlib.sha256(b'BON').hexdigest()


def test_session_associee_refuse_fichier_disparu(tmp_path: Path) -> None:
    config = _config(tmp_path)
    token = _session(config, {'piece.pdf': b'BON'})['token']
    televersement.recevoir_fichiers_televersement_lot(
        config, token, chemins_relatives=['piece.pdf'], flux=[BytesIO(b'BON')]
    )
    televersement.terminer_session_televersement_lot(config, token)
    televersement.associer_lot_session_televersement(config, token, 1)
    fichier = config.database_path.parent / 'uploads' / 'lots-televerses' / token / 'fichiers' / 'piece.pdf'
    fichier.unlink()
    with pytest.raises(televersement.TeleversementLotConflit):
        televersement.racine_session_televersement_lot(config, token)


def test_reprise_lot_associe_refuse_source_modifiee(tmp_path: Path) -> None:
    config = _config(tmp_path)
    token = _session(config, {'piece.pdf': b'BON'})['token']
    televersement.recevoir_fichiers_televersement_lot(
        config, token, chemins_relatives=['piece.pdf'], flux=[BytesIO(b'BON')]
    )
    televersement.terminer_session_televersement_lot(config, token)
    cree = televersement.creer_lot_session_televersement(config, token, creer_lot=lambda racine: 1)
    assert cree['lot_cree'] is True
    fichier = config.database_path.parent / 'uploads' / 'lots-televerses' / token / 'fichiers' / 'piece.pdf'
    fichier.write_bytes(b'BAD')
    with pytest.raises(televersement.TeleversementLotConflit, match='intégrité'):
        televersement.creer_lot_session_televersement(config, token, creer_lot=lambda racine: 2)


def test_finalisation_refuse_lien_vers_fichier_exterieur(tmp_path: Path) -> None:
    config = _config(tmp_path)
    token = _session(config, {'piece.pdf': b'BON'})['token']
    televersement.recevoir_fichiers_televersement_lot(
        config, token, chemins_relatives=['piece.pdf'], flux=[BytesIO(b'BON')]
    )
    fichier = config.database_path.parent / 'uploads' / 'lots-televerses' / token / 'fichiers' / 'piece.pdf'
    externe = tmp_path / 'externe.pdf'
    externe.write_bytes(b'BON')
    fichier.unlink()
    fichier.symlink_to(externe)
    with pytest.raises(televersement.TeleversementLotConflit):
        televersement.terminer_session_televersement_lot(config, token)


def test_envoi_refuse_parent_lien_vers_exterieur(tmp_path: Path) -> None:
    config = _config(tmp_path)
    token = _session(config, {'dossier/piece.pdf': b'BON'})['token']
    racine = config.database_path.parent / 'uploads' / 'lots-televerses' / token / 'fichiers'
    externe = tmp_path / 'externe'
    externe.mkdir()
    (racine / 'dossier').symlink_to(externe, target_is_directory=True)
    with pytest.raises(televersement.TeleversementLotConflit):
        televersement.recevoir_fichiers_televersement_lot(
            config, token, chemins_relatives=['dossier/piece.pdf'], flux=[BytesIO(b'BON')]
        )
    assert not (externe / 'piece.pdf').exists()


def test_repli_sans_fcntl_serialise_deux_threads(tmp_path: Path, monkeypatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    config = _config(tmp_path)
    token = _session(config, {'a.txt': b'a'})['token']
    monkeypatch.setattr(televersement, 'fcntl', None)
    essaye = Event()
    entre = Event()

    def concurrent() -> None:
        essaye.set()
        with televersement._verrou_session(config, token):
            entre.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with televersement._verrou_session(config, token):
            futur = pool.submit(concurrent)
            assert essaye.wait(5)
            est_entre_trop_tot = entre.wait(.2)
        futur.result(timeout=5)
    assert not est_entre_trop_tot
    assert entre.is_set()


def _process_verrou_repli(config, token, avant, dedans, attente):
    from seamtech_search.fiches import televersement_lot as module

    module.fcntl = None
    avant.set()
    with module._verrou_session(config, token):
        dedans.set()
        attente.wait(10)


def test_repli_sans_fcntl_serialise_deux_processus(tmp_path: Path, monkeypatch) -> None:
    import multiprocessing

    config = _config(tmp_path)
    token = _session(config, {'a.txt': b'a'})['token']
    monkeypatch.setattr(televersement, 'fcntl', None)
    contexte = multiprocessing.get_context('spawn')
    avant, dedans, fin = contexte.Event(), contexte.Event(), contexte.Event()
    processus = contexte.Process(target=_process_verrou_repli, args=(config, token, avant, dedans, fin))
    try:
        with televersement._verrou_session(config, token):
            processus.start()
            assert avant.wait(10)
            est_entre_trop_tot = dedans.wait(.5)
        assert dedans.wait(10)
        fin.set()
        processus.join(10)
        assert not est_entre_trop_tot
        assert processus.exitcode == 0
    finally:
        fin.set()
        if processus.is_alive():
            processus.terminate()
            processus.join(10)


def test_repli_sans_fcntl_libere_verrou_apres_crash(tmp_path: Path, monkeypatch) -> None:
    import multiprocessing

    config = _config(tmp_path)
    token = _session(config, {'a.txt': b'a'})['token']
    monkeypatch.setattr(televersement, 'fcntl', None)
    contexte = multiprocessing.get_context('spawn')
    avant, dedans, fin = contexte.Event(), contexte.Event(), contexte.Event()
    processus = contexte.Process(target=_process_verrou_repli, args=(config, token, avant, dedans, fin))
    try:
        processus.start()
        assert dedans.wait(10)
        processus.kill()
        processus.join(10)
        assert not processus.is_alive()
        with televersement._verrou_session(config, token):
            pass
    finally:
        # Ne pas toucher à l'Event après SIGKILL : un sémaphore peut être
        # resté pris par le processus mort.
        if processus.is_alive():
            processus.terminate()
            processus.join(10)


def _envoi_process_repli(config, token, debut, pret):
    from io import BytesIO

    from seamtech_search.fiches import televersement_lot as module

    module.fcntl = None
    pret.wait(10)
    noms = [f'fichier-{indice}.txt' for indice in range(debut, debut + 10)]
    module.recevoir_fichiers_televersement_lot(
        config, token, chemins_relatives=noms, flux=[BytesIO(b'x') for _ in noms],
    )


def test_repli_sans_fcntl_envois_concurrents_sans_perte(tmp_path: Path, monkeypatch) -> None:
    import multiprocessing

    config = _config(tmp_path)
    fichiers = {f'fichier-{indice}.txt': b'x' for indice in range(20)}
    token = _session(config, fichiers)['token']
    monkeypatch.setattr(televersement, 'fcntl', None)
    contexte = multiprocessing.get_context('spawn')
    pret = contexte.Event()
    processus = [contexte.Process(target=_envoi_process_repli, args=(config, token, debut, pret)) for debut in (0, 10)]
    try:
        for process in processus:
            process.start()
        pret.set()
        for process in processus:
            process.join(30)
        assert [process.exitcode for process in processus] == [0, 0]
        etat = televersement.terminer_session_televersement_lot(config, token)
        assert etat['nombre_fichiers_recus'] == 20
        racine = televersement.racine_session_televersement_lot(config, token)
        assert all((racine / nom).read_bytes() == contenu for nom, contenu in fichiers.items())
    finally:
        for process in processus:
            if process.is_alive():
                process.terminate()
                process.join(10)


def test_repli_sans_fcntl_refuse_erreur_de_verrou(tmp_path: Path, monkeypatch) -> None:
    import sqlite3

    config = _config(tmp_path)
    token = _session(config, {'a.txt': b'a'})['token']
    monkeypatch.setattr(televersement, 'fcntl', None)

    def erreur(*_args, **_kwargs):
        raise sqlite3.OperationalError('erreur synthetique')

    monkeypatch.setattr(televersement.sqlite3, 'connect', erreur)
    with pytest.raises(televersement.TeleversementLotConflit, match='Verrou de session indisponible'):
        with televersement._verrou_session(config, token):
            pytest.fail('section critique non protégée')


def test_verrous_fcntl_et_repli_ne_se_contredisent_pas(tmp_path: Path) -> None:
    import multiprocessing

    if televersement.fcntl is None:
        pytest.skip('comparaison des deux modes uniquement quand fcntl est disponible')
    config = _config(tmp_path)
    token = _session(config, {'a.txt': b'a'})['token']
    contexte = multiprocessing.get_context('spawn')
    avant, dedans, fin = contexte.Event(), contexte.Event(), contexte.Event()
    processus = contexte.Process(target=_process_verrou_repli, args=(config, token, avant, dedans, fin))
    try:
        with televersement._verrou_session(config, token):
            processus.start()
            assert avant.wait(10)
            est_entre_trop_tot = dedans.wait(.5)
        assert dedans.wait(10)
        fin.set()
        processus.join(10)
        assert not est_entre_trop_tot
        assert processus.exitcode == 0
    finally:
        fin.set()
        if processus.is_alive():
            processus.terminate()
            processus.join(10)


def test_api_creation_idempotente_et_capacite(tmp_path: Path) -> None:
    client, _ = _client_api(tmp_path)
    capacites = client.get("/imports/dossier/televersements/capacites")
    assert capacites.status_code == 200
    assert capacites.json()["creation_idempotente"] == 1
    corps = {"nom_racine": "DOSSIER-SYNTHESE", "nombre_fichiers": 1, "taille_totale_octets": 3, "cle_idempotence": "ab" * 32}
    endpoint = "/imports/dossier/televersements/v2"
    premiere = client.post(endpoint, json=corps)
    seconde = client.post(endpoint, json=corps)
    assert premiere.status_code == seconde.status_code == 201
    assert premiere.json()["token"] == seconde.json()["token"]
    assert premiere.json()["idempotence_creation"] == 1
    assert client.post(endpoint, json={**corps, "taille_totale_octets": 4}).status_code == 409
    assert client.post(endpoint, json={**corps, "cle_idempotence": "invalide"}).status_code == 422
    assert client.post(endpoint, json={k: v for k, v in corps.items() if k != "cle_idempotence"}).status_code == 422
    assert client.post("/imports/dossier/televersements", json=corps).status_code == 422


def test_api_registre_sature_refuse_nouvelles_sessions_mais_pas_rejeux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client_api(tmp_path)
    monkeypatch.setattr(televersement, "MAX_RESERVATIONS_CREATION", 1)
    endpoint = "/imports/dossier/televersements/v2"
    corps = {"nom_racine": "DOSSIER-SYNTHESE", "nombre_fichiers": 1, "taille_totale_octets": 3, "cle_idempotence": "ab" * 32}
    premiere = client.post(endpoint, json=corps)
    assert premiere.status_code == 201
    assert client.post(endpoint, json={**corps, "cle_idempotence": "cd" * 32}).status_code == 507
    reprise = client.post(endpoint, json=corps)
    assert reprise.status_code == 201
    assert reprise.json()["token"] == premiere.json()["token"]


@pytest.mark.parametrize("phase", ["lecture", "publication"])
def test_erreur_televersement_masque_details_et_trace(tmp_path: Path, monkeypatch, phase: str):
    import traceback
    from io import BytesIO
    from types import SimpleNamespace

    from seamtech_search.fiches import televersement_lot as t

    config = SimpleNamespace(database_path=tmp_path / "data" / "search.db", max_file_size_bytes=1024,
                             min_free_bytes=0)
    token = t.creer_session_televersement_lot(config, nom_racine="TEST", nombre_fichiers=1,
                                              taille_totale_octets=4)["token"]
    class LecturePanne(BytesIO):
        def read(self, *args):
            raise OSError("chemin-secret-synthetique")
    if phase == "lecture":
        source = LecturePanne(b"test")
    else:
        source = BytesIO(b"test")
        monkeypatch.setattr(t.os, "replace", lambda *_a: (_ for _ in ()).throw(OSError("chemin-secret-synthetique")))
    with pytest.raises(t.TeleversementLotErreur) as erreur:
        t.recevoir_fichiers_televersement_lot(config, token, chemins_relatives=["a.txt"], flux=[source])
    trace = "".join(traceback.format_exception(erreur.value))
    assert "chemin-secret-synthetique" not in trace
    dossier, manifeste = t._charger_manifeste(config, token)
    assert not manifeste["fichiers"]
    assert not list((dossier / t.DOSSIER_TEMPORAIRE).iterdir())
    assert not (dossier / "fichiers" / "a.txt").exists()
