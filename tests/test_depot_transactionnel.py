"""Lot C — dépôt transactionnel d'un dossier (§17.11 : nom de fichier imposé).

« Écriture entière ou pas du tout, rejeu sans doublon » : fiche, pièces
jointes et ligne de lot forment une seule unité ; un crash à mi-écriture ne
laisse RIEN en base ; rejouer un dossier ne duplique rien.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Iterator

import pytest

from seamtech_search.fiches import depot
from seamtech_search.fiches.depot import DepotImpossible, empreintes_arbre, scanner_dossier
from seamtech_search.fiches.gabarits import GABARITS_EMBARQUES

RACINE = Path(__file__).resolve().parent.parent
ARCHIVE_7792 = RACINE / "sample_data/CLIENT-7792-SO"
ARCHIVE_GENOIS = RACINE / "sample_data/CLIENT-GENOA"
URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.postgres  # le dépôt exige PostgreSQL réel (tables fiche_*, §17.1)


# ---------------------------------------------------------------------------
# Scanner : lecture seule, aucun PDF détectable = refus documenté.
# ---------------------------------------------------------------------------


class TestScannerUnitaire:
    def test_plan_complet_d_un_dossier_reel(self, tmp_path: Path) -> None:
        dossier = tmp_path / "D0001"
        dossier.mkdir()
        shutil.copy(ARCHIVE_7792 / "fiche-7792-SO_ffab.pdf", dossier / "fiche.pdf")
        (dossier / "croquis.md").write_text("notes atelier", encoding="utf-8")
        plan = scanner_dossier(dossier, list(GABARITS_EMBARQUES))
        assert plan.accepte and plan.gabarit_code == "FICHE_PORTANT_V1"
        assert plan.pdf_fiche is not None and plan.pdf_fiche.name == "fiche.pdf"
        assert [piece.role for piece in plan.pieces] == ["piece_jointe"]
        assert plan.pieces[0].empreinte_sha256

    def test_dossier_sans_pdf_est_refuse_avec_raison(self, tmp_path: Path) -> None:
        dossier = tmp_path / "D0002"
        dossier.mkdir()
        (dossier / "notes.txt").write_text("pas de fiche ici", encoding="utf-8")
        plan = scanner_dossier(dossier, list(GABARITS_EMBARQUES))
        assert not plan.accepte and "aucun PDF" in plan.raison_refus

    def test_pdf_illisible_est_refuse_avec_scores(self, tmp_path: Path) -> None:
        dossier = tmp_path / "D0003"
        dossier.mkdir()
        (dossier / "corrompu.pdf").write_bytes(b"%PDF-1.4 pas un vrai PDF")
        plan = scanner_dossier(dossier, list(GABARITS_EMBARQUES))
        assert not plan.accepte
        assert "gabarit inconnu" in plan.raison_refus and "corrompu.pdf=-1" in plan.raison_refus

    def test_dossier_absent_leve_avec_chemin(self, tmp_path: Path) -> None:
        with pytest.raises(DepotImpossible, match="introuvable"):
            scanner_dossier(tmp_path / "absent", list(GABARITS_EMBARQUES))

    def test_genois_detecte_dans_son_dossier(self, tmp_path: Path) -> None:
        dossier = tmp_path / "D0004"
        dossier.mkdir()
        shutil.copy(ARCHIVE_GENOIS / "fiche-genois.pdf", dossier / "fiche.pdf")
        plan = scanner_dossier(dossier, list(GABARITS_EMBARQUES))
        assert plan.accepte and plan.gabarit_code == "FICHE_GENOIS_V1"


# ---------------------------------------------------------------------------
# Dépôt transactionnel : PostgreSQL réel.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def base_depot() -> Iterator[dict[str, Any]]:
    """Base jetable migrée (001→010) + gabarits embarqués."""
    import uuid

    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    nom_base = f"depot_test_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{nom_base}"')
    finally:
        admin.close()
    url_base = URL_PG.rsplit("/", 1)[0] + f"/{nom_base}"
    from seamtech_search.fiches.gabarits import initialiser_gabarits
    from seamtech_search.indexer import SearchIndex

    index = SearchIndex(Path(f"/tmp/unused-depot-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    initialiser_gabarits(index)
    try:
        yield {"index": index, "url": url_base}
    finally:
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
def dossier_ok(tmp_path: Path, base_depot: dict) -> Path:
    dossier = tmp_path / "AFFAIRE-0001"
    dossier.mkdir()
    shutil.copy(ARCHIVE_7792 / "fiche-7792-SO_ffab.pdf", dossier / "fiche-7792-SO_ffab.pdf")
    (dossier / "croquis-atelier.txt").write_text("repère des renforts", encoding="utf-8")
    return dossier


def _comptes(index: Any) -> dict[str, int]:
    requetes = {
        "fiche": "SELECT COUNT(*) FROM fiche",
        "pieces": "SELECT COUNT(*) FROM fiche_piece_jointe",
        "champs": "SELECT COUNT(*) FROM fiche_champ_extrait",
        "lots": "SELECT COUNT(*) FROM lot_import",
        "lignes": "SELECT COUNT(*) FROM lot_dossier",
    }
    comptes: dict[str, int] = {}
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            for nom, requete in requetes.items():
                cursor.execute(requete)
                comptes[nom] = int(cursor.fetchone()[0])
    return comptes


class TestEcritureEntiereOuPasDuTout:
    def test_depot_complet_fiche_pieces_lot(self, base_depot: dict, dossier_ok: Path) -> None:
        index = base_depot["index"]
        resultat = depot.deposer_dossier(index, dossier_ok)
        assert resultat["statut"] == "traite" and resultat["fiche"] == "7792-SO"
        assert resultat["pieces"] == 1 and resultat["id_lot"] > 0
        comptes = _comptes(index)
        assert comptes["fiche"] == 1 and comptes["pieces"] == 1 and comptes["lignes"] == 1
        etat = depot.etat_lot(index, resultat["id_lot"])
        assert etat["statut"] == "termine" and etat["nb_traites"] == 1
        ligne = etat["dossiers"][0]
        assert ligne["statut"] == "traite" and ligne["id_fiche"] is not None

    def test_fiche_en_a_valider_jamais_valide(self, base_depot: dict, dossier_ok: Path) -> None:
        index = base_depot["index"]
        depot.deposer_dossier(index, dossier_ok)
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("SELECT statut FROM fiche")
                assert cursor.fetchone()[0] == "a_valider"  # RG3

    def test_crash_apres_ecriture_fiche_rien_ne_persiste(self, base_depot: dict, dossier_ok: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """« Écriture entière ou pas du tout » : un crash APRÈS l'écriture de la
        fiche annule TOUT (la pièce jointe et la ligne de lot n'existent pas,
        la fiche non plus — transaction annulée par l'appelant)."""
        index = base_depot["index"]
        vraie_ecriture = depot.ecrire_fiche

        def ecrire_puis_crash(idx: Any, fiche: Any, connexion: Any = None) -> tuple[int, str]:
            id_fiche, action = vraie_ecriture(idx, fiche, connexion=connexion)
            raise RuntimeError("crash simulé après l'écriture de la fiche")

        monkeypatch.setattr(depot, "ecrire_fiche", ecrire_puis_crash)
        avant = _comptes(index)
        resultat = depot.deposer_dossier(index, dossier_ok)
        assert resultat["statut"] == "echec"
        assert "écriture annulée : RuntimeError" in resultat["raison"]
        apres = _comptes(index)
        assert apres["fiche"] == avant["fiche"], "la fiche a survécu au crash : le tout-ou-rien est cassé"
        assert apres["pieces"] == avant["pieces"]
        # le suivi de lot, LUI, est une couche distincte : la tentative est tracée
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("SELECT statut, raison FROM lot_dossier WHERE id_lot = %s", (resultat["id_lot"],))
                statut, raison = cursor.fetchone()
        assert statut == "echec" and "crash simulé" in raison

    def test_dossier_sans_fiche_identifiable_liste_avec_raison(self, base_depot: dict, tmp_path: Path) -> None:
        index = base_depot["index"]
        vide = tmp_path / "SANS-FICHE"
        vide.mkdir()
        (vide / "notes.txt").write_text("rien", encoding="utf-8")
        resultat = depot.deposer_dossier(index, vide)
        assert resultat["statut"] == "echec" and "aucun PDF" in resultat["raison"]
        etat = depot.etat_lot(index, resultat["id_lot"])
        assert etat["dossiers"][0]["raison"] == resultat["raison"]  # listé, pas un crash


class TestRejeuSansDoublon:
    def test_rejouer_ne_duplique_rien(self, base_depot: dict, dossier_ok: Path) -> None:
        index = base_depot["index"]
        premier = depot.deposer_dossier(index, dossier_ok)
        avant = _comptes(index)
        second = depot.deposer_dossier(index, dossier_ok)
        apres = _comptes(index)
        assert premier["statut"] == "traite" and second["statut"] == "deja_traite"
        # les DONNÉES ne bougent pas (la traçabilité, elle, trace chaque tentative) :
        assert apres["fiche"] == avant["fiche"] and apres["pieces"] == avant["pieces"]
        assert apres["champs"] == avant["champs"], "le rejeu a dupliqué quelque chose"
        # le lot isolé de la 2e tentative (deja_traite) doit être TERMINE, pas en_cours
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("SELECT statut FROM lot_import WHERE id_lot = %s", (second["id_lot"],))
                assert cursor.fetchone()[0] == "termine", "le lot du rejeu est resté en_cours"
        assert "deja_traite" in second["raison"]

    def test_cle_idempotence_normcase_plus_empreinte(self, dossier_ok: Path) -> None:
        empreinte = depot.empreinte_fichier(dossier_ok / "fiche-7792-SO_ffab.pdf")
        cle = depot.cle_idempotence(dossier_ok / "fiche-7792-SO_ffab.pdf", empreinte)
        # même normalisation que le crawler (os.path.normcase) + SHA-256
        assert cle == os.path.normcase(str((dossier_ok / "fiche-7792-SO_ffab.pdf").resolve())) + "|" + empreinte
        # une empreinte différente (fiche corrigée) donne une clé différente
        autre = depot.cle_idempotence(dossier_ok / "fiche-7792-SO_ffab.pdf", "0" * 64)
        assert autre != cle

    def test_pieces_jointes_stables_au_rejeu(self, base_depot: dict, dossier_ok: Path) -> None:
        index = base_depot["index"]
        depot.deposer_dossier(index, dossier_ok)
        premier = _comptes(index)["pieces"]
        depot.deposer_dossier(index, dossier_ok)
        assert _comptes(index)["pieces"] == premier


class TestArchiveIntacteRG13:
    def test_empreintes_avant_apres_identiques(self, base_depot: dict, dossier_ok: Path) -> None:
        index = base_depot["index"]
        avant = empreintes_arbre(dossier_ok)
        listing_avant = sorted(chemin.name for chemin in dossier_ok.iterdir())
        depot.deposer_dossier(index, dossier_ok)
        apres = empreintes_arbre(dossier_ok)
        listing_apres = sorted(chemin.name for chemin in dossier_ok.iterdir())
        assert avant == apres, "un fichier de l'archive a été modifié (RG13)"
        assert listing_avant == listing_apres, "un fichier a été créé ou supprimé dans le dossier déposé"
