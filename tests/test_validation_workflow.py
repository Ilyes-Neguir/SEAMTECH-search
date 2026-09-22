"""Lot D — workflow de validation (§17.11, fichier imposé).

Transitions de statut (valider / rejeter / rouvrir), verrous RG11 (une valeur
corrigée par un humain n'est jamais écrasée — côté API ET côté dépôt), journal
fiche_validation, file de validation (comptes PAR PALIER, jamais de moyenne),
verrou de calibration sur POST /validation/lot (409 tant que calibre:false).
HTTP réel via TestClient sur PostgreSQL live.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from seamtech_search.config import AppConfig

RACINE = Path(__file__).resolve().parent.parent
URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.postgres  # couche métier PostgreSQL uniquement (§17.1)


@pytest.fixture(scope="module")
def base_workflow() -> Iterator[dict[str, Any]]:
    """Base jetable (migrations + gabarits) puis dépôt de DEUX fiches réelles
    ``a_valider`` (7792-SO, 0901-MM) par le moteur du Lot C (RG3)."""
    import shutil
    import uuid

    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    nom_base = f"valid_test_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{nom_base}"')
    finally:
        admin.close()
    url_base = URL_PG.rsplit("/", 1)[0] + f"/{nom_base}"
    from seamtech_search.fiches import depot
    from seamtech_search.fiches.gabarits import initialiser_gabarits
    from seamtech_search.indexer import SearchIndex

    index = SearchIndex(Path(f"/tmp/unused-valid-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    initialiser_gabarits(index)
    racine_essai = Path(f"/tmp/valid-dossiers-{nom_base}")
    for nom, source in (
        ("CLIENT-7792-SO", RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"),
        ("CLIENT-GENOA", RACINE / "sample_data/CLIENT-GENOA/fiche-genois.pdf"),
    ):
        dossier = racine_essai / nom
        dossier.mkdir(parents=True)
        (dossier / "fiche.pdf").write_bytes(source.read_bytes())
        depot.deposer_dossier(index, dossier)
    try:
        yield {"index": index, "url": url_base, "racine_essai": racine_essai}
    finally:
        shutil.rmtree(racine_essai, ignore_errors=True)
        index.close()
        admin = psycopg2.connect(URL_PG)
        admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin.cursor() as cursor:
                cursor.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()", (nom_base,))
                cursor.execute(f'DROP DATABASE IF EXISTS "{nom_base}"')
        finally:
            admin.close()


@pytest.fixture()
def client(base_workflow: dict, tmp_path: Path) -> TestClient:
    from seamtech_search.api import create_app

    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "unused.db",
        database_url=base_workflow["url"],
    )
    return TestClient(create_app(config))


def _statut(base: dict, code: str) -> str:
    with base["index"].connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT statut FROM fiche WHERE code = %s", (code,))
            ligne = cursor.fetchone()
            assert ligne is not None, f"fiche {code} absente"
            return str(ligne[0])


def _journal(base: dict, code: str) -> list[tuple]:
    with base["index"].connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "SELECT v.action, v.etat_avant, v.etat_apres FROM fiche_validation v "
                "JOIN fiche f ON f.id_fiche = v.id_fiche WHERE f.code = %s ORDER BY v.id_validation",
                (code,),
            )
            return cursor.fetchall()


def _dossier_reprise(base: dict, nom: str) -> Path:
    """Un dossier neuf portant le même PDF (pour rejouer une extraction)."""
    dossier = base["racine_essai"] / nom
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "fiche.pdf").write_bytes((RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf").read_bytes())
    return dossier


class TestTransitionsDeStatut:
    def test_valider_une_fiche_a_valider(self, base_workflow: dict, client: TestClient) -> None:
        assert _statut(base_workflow, "7792-SO") == "a_valider"  # RG3
        reponse = client.post("/fiches/7792-SO/valider", json={"utilisateur": "alice", "commentaire": "conforme"})
        assert reponse.status_code == 200 and reponse.json()["statut"] == "valide"
        assert _statut(base_workflow, "7792-SO") == "valide"
        assert ("valider", "a_valider", "valide") in _journal(base_workflow, "7792-SO")

    def test_valider_deux_fois_refuse(self, base_workflow: dict, client: TestClient) -> None:
        reponse = client.post("/fiches/7792-SO/valider", json={"utilisateur": "alice"})
        assert reponse.status_code == 409 and "a_valider" in reponse.json()["detail"]

    def test_rouvrir_une_fiche_validee(self, base_workflow: dict, client: TestClient) -> None:
        reponse = client.post("/fiches/7792-SO/rouvrir", json={"utilisateur": "bob", "commentaire": "coquille vue"})
        assert reponse.status_code == 200 and reponse.json()["statut"] == "a_valider"
        assert _statut(base_workflow, "7792-SO") == "a_valider"
        assert ("rouvrir", "valide", "a_valider") in _journal(base_workflow, "7792-SO")

    def test_rejeter_sans_motif_refuse_avec_motif_ok(self, base_workflow: dict, client: TestClient) -> None:
        reponse = client.post("/fiches/7792-SO/rejeter", json={"utilisateur": "bob"})
        assert reponse.status_code == 422 and "motif" in reponse.json()["detail"].lower()
        reponse = client.post("/fiches/7792-SO/rejeter", json={"utilisateur": "bob", "motif": "gabarit douteux"})
        assert reponse.status_code == 200 and reponse.json()["statut"] == "rejete"
        assert ("rejeter", "a_valider", "rejete") in _journal(base_workflow, "7792-SO")
        # rejeter une fiche rejetée → 409 ; rouvrir la rend a_valider
        assert client.post("/fiches/7792-SO/rejeter", json={"utilisateur": "bob", "motif": "x"}).status_code == 409
        assert client.post("/fiches/7792-SO/rouvrir", json={"utilisateur": "bob"}).status_code == 200

    def test_fiche_inconnue_404(self, client: TestClient) -> None:
        assert client.post("/fiches/INCONNU/valider", json={"utilisateur": "alice"}).status_code == 404
        assert client.post("/fiches/INCONNU/rejeter", json={"utilisateur": "alice", "motif": "x"}).status_code == 404


class TestCorrectionEtJournal:
    def test_corriger_un_champ_trace_tout(self, base_workflow: dict, client: TestClient) -> None:
        reponse = client.post(
            "/fiches/7792-SO/corriger",
            json={"champ": "fiche.designation", "valeur": "Spi Asymétrique Medium Régate 2", "utilisateur": "carole"},
        )
        assert reponse.status_code == 200
        corps = reponse.json()
        assert corps["apres"] == "Spi Asymétrique Medium Régate 2"
        with base_workflow["index"].connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    "SELECT c.valeur_normalisee, c.corrige, u.identifiant FROM fiche_champ_extrait c "
                    "JOIN fiche f ON f.id_fiche = c.id_fiche "
                    "LEFT JOIN utilisateur u ON u.id_utilisateur = c.corrige_par "
                    "WHERE f.code = %s AND c.champ = %s",
                    ("7792-SO", "fiche.designation"),
                )
                valeur, corrige, par = cursor.fetchone()
        assert valeur == "Spi Asymétrique Medium Régate 2" and corrige is True and par == "carole"
        assert any(action == "corriger" for action, _, _ in _journal(base_workflow, "7792-SO"))

    def test_verrou_rg11_cote_depot_re_extraction_refusee(self, base_workflow: dict, tmp_path: Path) -> None:
        """RG11 côté dépôt : ré-extraction d'une fiche portant une correction
        humaine → dossier en ÉCHEC avec la raison, fiche et corrections intactes."""
        from seamtech_search.fiches import depot

        index = base_workflow["index"]
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM fiche_champ_extrait c JOIN fiche f ON f.id_fiche = c.id_fiche "
                    "WHERE f.code = %s AND c.corrige",
                    ("7792-SO",),
                )
                assert int(cursor.fetchone()[0]) >= 1, "le scénario exige une fiche avec correction"
        resultat = depot.deposer_dossier(index, _dossier_reprise(base_workflow, "AFFAIRE-RG11"))
        assert resultat["statut"] == "echec" and "RG11" in (resultat["raison"] or ""), resultat
        # la fiche (a_valider, c'est le verrou corrections qui a parlé) et sa
        # correction sont intactes
        assert _statut(base_workflow, "7792-SO") == "a_valider"
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    "SELECT c.valeur_normalisee FROM fiche_champ_extrait c JOIN fiche f ON f.id_fiche = c.id_fiche "
                    "WHERE f.code = %s AND c.corrige",
                    ("7792-SO",),
                )
                assert cursor.fetchone()[0] == "Spi Asymétrique Medium Régate 2"

    def test_corriger_une_fiche_validee_refuse_rg11(self, base_workflow: dict, client: TestClient) -> None:
        assert client.post("/fiches/7792-SO/valider", json={"utilisateur": "alice"}).status_code == 200
        reponse = client.post(
            "/fiches/7792-SO/corriger",
            json={"champ": "fiche.designation", "valeur": "x", "utilisateur": "carole"},
        )
        assert reponse.status_code == 409 and "RG11" in reponse.json()["detail"]

    def test_rouvrir_avec_effacer_corrections_leve_le_verrou(self, base_workflow: dict, client: TestClient) -> None:
        from seamtech_search.fiches import depot

        reponse = client.post(
            "/fiches/7792-SO/rouvrir",
            json={"utilisateur": "bob", "effacer_corrections": True, "commentaire": "arbitrage : corrections abandonnées"},
        )
        assert reponse.status_code == 200 and reponse.json()["corrections_effacees"] is True
        with base_workflow["index"].connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM fiche_champ_extrait c JOIN fiche f ON f.id_fiche = c.id_fiche "
                    "WHERE f.code = %s AND c.corrige",
                    ("7792-SO",),
                )
                assert int(cursor.fetchone()[0]) == 0
        # la ré-extraction redevient possible (remplacement sur place, id conservé)
        resultat = depot.deposer_dossier(base_workflow["index"], _dossier_reprise(base_workflow, "AFFAIRE-RG11-2"))
        assert resultat["statut"] == "traite" and "remplacee" in (resultat["raison"] or ""), resultat


class TestFileDeValidation:
    def test_file_triee_par_confiance_avec_paliers(self, base_workflow: dict, client: TestClient) -> None:
        reponse = client.get("/validation/file")
        assert reponse.status_code == 200
        file = reponse.json()
        assert file, "la file de validation ne doit pas être vide"
        confiances = [f["confiance_min"] for f in file]
        assert confiances == sorted(confiances, key=lambda x: (x is None, x)), "file non triée (incertaines d'abord)"
        for entree in file:
            assert set(entree["paliers"]) == {"certain", "lu", "decompose", "partiel"}
            # JAMAIS de « confiance moyenne » : des comptes ordinaires, additionnables
            assert sum(entree["paliers"].values()) == entree["nb_champs"]

    def test_filtre_par_gabarit(self, client: TestClient) -> None:
        reponse = client.get("/validation/file", params={"gabarit": "FICHE_PORTANT_V1"})
        assert reponse.status_code == 200
        assert all(f["gabarit"] == "FICHE_PORTANT_V1" for f in reponse.json())
        assert reponse.json(), "la fiche 7792-SO (portant) doit être dans la file filtrée"
        reponse = client.get("/validation/file", params={"gabarit": "INCONNU"})
        assert reponse.status_code == 200 and reponse.json() == []


class TestVerrouCalibration:
    def test_validation_lot_refusee_tant_que_non_calibre(self, base_workflow: dict, client: TestClient) -> None:
        """calibre:false (état du dépôt jusqu'aux fiches réelles) → 409, rien n'est validé."""
        reponse = client.post("/validation/lot", json={"codes": ["0901-MM"], "utilisateur": "alice"})
        assert reponse.status_code == 409
        assert "calibr" in reponse.json()["detail"].lower()
        assert _statut(base_workflow, "0901-MM") == "a_valider", "la 409 ne doit rien écrire"

    def test_validation_lot_avec_acquittement_explicite(self, base_workflow: dict, client: TestClient) -> None:
        reponse = client.post(
            "/validation/lot",
            json={"codes": ["0901-MM"], "utilisateur": "chef", "acquittement_humain": True, "commentaire": "acquittement chef d'atelier"},
        )
        assert reponse.status_code == 200
        assert reponse.json()["nb_validees"] == 1
        assert ("valider", "a_valider", "valide") in _journal(base_workflow, "0901-MM")

    def test_validation_lot_ignores_avec_raison(self, base_workflow: dict, client: TestClient) -> None:
        reponse = client.post(
            "/validation/lot",
            json={"codes": ["0901-MM", "INCONNU-1"], "utilisateur": "chef", "acquittement_humain": True},
        )
        assert reponse.status_code == 200
        corps = reponse.json()
        assert corps["nb_validees"] == 0  # 0901-MM déjà validée au test précédent
        assert {"code": "0901-MM", "raison": "statut « valide » — rouvrez d'abord"} == corps["ignorees"][0]
        assert corps["ignorees"][1]["raison"] == "fiche inconnue"

    def test_validation_lot_calibree_passee_le_verrou(self, base_workflow: dict, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Une calibration réelle (fichier calibre:true) lève le verrou sans acquittement."""
        import json

        rouvrir = client.post("/fiches/0901-MM/rouvrir", json={"utilisateur": "chef"})
        assert rouvrir.status_code == 200
        seuils = tmp_path / "seuils_calibres.json"
        seuils.write_text(
            json.dumps({"version": 1, "calibre": True, "calibre_le": "2026-09-21", "fiches_reelles_utilisees": 20,
                        "passage_direct": 0.99, "revue_manuelle": 0.90, "reprise_complete": 0.85}),
            encoding="utf-8",
        )
        monkeypatch.setenv("SEAMTECH_SEUILS_CONFIANCE", str(seuils))
        reponse = client.post("/validation/lot", json={"codes": ["0901-MM"], "utilisateur": "alice"})
        assert reponse.status_code == 200, reponse.json()
        assert reponse.json()["nb_validees"] == 1


class TestListeEtPieces:
    def test_liste_fiches_avec_facettes(self, base_workflow: dict, client: TestClient) -> None:
        reponse = client.get("/fiches")
        assert reponse.status_code == 200
        corps = reponse.json()
        codes = {f["code"] for f in corps["fiches"]}
        assert {"7792-SO", "0901-MM"} <= codes
        assert corps["facettes"].get("a_valider", 0) >= 1
        assert corps["total"] == len(corps["fiches"]) or corps["total"] >= 2
        portant = next(f for f in corps["fiches"] if f["code"] == "7792-SO")
        assert portant["gabarit"] == "FICHE_PORTANT_V1" and portant["client"] == "Sailonet"
        # filtre par statut
        reponse = client.get("/fiches", params={"statut": "inexistant"})
        assert reponse.status_code == 200 and reponse.json()["fiches"] == []

    def test_pieces_jointes_avec_description_documents(self, base_workflow: dict, client: TestClient) -> None:
        """RG12 : la pièce déposée apparaît ici avec sa référence catalogue."""
        from seamtech_search.fiches import depot

        dossier = base_workflow["racine_essai"] / "CLIENT-PIECE"
        dossier.mkdir(parents=True, exist_ok=True)
        (dossier / "fiche.pdf").write_bytes((RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf").read_bytes())
        (dossier / "croquis-voile.pdf").write_bytes(b"%PDF-1.4 croquis")
        depot.deposer_dossier(base_workflow["index"], dossier)
        reponse = client.get("/fiches/7792-SO/pieces")
        assert reponse.status_code == 200
        corps_pieces = reponse.json()
        assert corps_pieces["fichier_source"], "le chemin du PDF source doit être exposé (visionneuse lot D)"
        assert corps_pieces["pdf_source"], "le chemin d'archive du PDF (clé d'idempotence lot C) doit être exposé"
        cible = next(p for p in corps_pieces["pieces"] if p["nom"] == "croquis-voile.pdf")
        assert cible["role"] == "piece_jointe" and cible["id_document"] is not None
        assert cible["empreinte_sha256"]
        assert client.get("/fiches/INCONNU/pieces").status_code == 404
