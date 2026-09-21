"""Lot C — lots suivis, reprenables, idempotents ; débit mesuré (§17.2).

Scénarios du critère d'acceptation : lot de 100 dossiers interrompu à
mi-parcours → reprise sans doublon ; rejeu complet → 0 nouvelle fiche ;
échecs listés avec leur raison ; archive intacte ; temps par dossier
extrapolé à 10 000 (mesuré, pas supposé).
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any, Iterator

import pytest

from seamtech_search.fiches import depot
from seamtech_search.fiches.depot import DepotImpossible
from seamtech_search.fiches.gabarits import initialiser_gabarits
from seamtech_search.indexer import SearchIndex

RACINE = Path(__file__).resolve().parent.parent
ARCHIVE_7792 = RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"
ARCHIVE_GENOIS = RACINE / "sample_data/CLIENT-GENOA/fiche-genois.pdf"
URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.postgres  # le dépôt exige PostgreSQL réel (tables fiche_*, §17.1)

NB_DOSSIERS = 100
NB_A_FICHE = 40  # 20 portants + 20 génois ; 60 dossiers sans fiche identifiable
NB_FICHES = 2  # RG11 : les 20 portants partagent le code « 7792-SO » (remplacements), idem génois


def _construire_archive(racine: Path) -> None:
    """Archive d'essai : 100 sous-dossiers — 20 portants, 20 génois, 60 dossiers
    « faibles » (sans PDF ou PDF illisible) qui doivent être LISTÉS en échec."""
    racine.mkdir(parents=True, exist_ok=True)
    for index in range(1, NB_DOSSIERS + 1):
        dossier = racine / f"AFFAIRE-{index:04d}"
        dossier.mkdir()
        if index <= 20:
            shutil.copy(ARCHIVE_7792, dossier / "fiche.pdf")
        elif index <= 40:
            shutil.copy(ARCHIVE_GENOIS, dossier / "fiche.pdf")
        elif index <= 70:
            (dossier / "notes.txt").write_text("pas de PDF dans ce dossier", encoding="utf-8")
        else:
            (dossier / "corrompu.pdf").write_bytes(b"%PDF-1.4 ceci n'est pas un PDF")


@pytest.fixture(scope="module")
def base_lot() -> Iterator[dict[str, Any]]:
    """Base jetable (001→010) + gabarits + archive de 100 dossiers."""
    import uuid

    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    nom_base = f"lot_test_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{nom_base}"')
    finally:
        admin.close()
    url_base = URL_PG.rsplit("/", 1)[0] + f"/{nom_base}"
    index = SearchIndex(Path(f"/tmp/unused-lot-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    initialiser_gabarits(index)
    # Chemin PORTABLE : jamais de domicile codé en dur — le bac à sable local
    # et le runner GitHub n'ont ni le même /home ni les mêmes droits.
    # (Échec mesuré en CI le 21/09 : PermissionError sur /home/user.)
    import tempfile

    archive = Path(tempfile.mkdtemp(prefix=f"pytest_lot_archive_{nom_base}_"))
    _construire_archive(archive)
    try:
        yield {"index": index, "url": url_base, "archive": archive}
    finally:
        index.close()
        shutil.rmtree(archive, ignore_errors=True)
        admin = psycopg2.connect(URL_PG)
        admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin.cursor() as cursor:
                cursor.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()", (nom_base,))
                cursor.execute(f'DROP DATABASE IF EXISTS "{nom_base}"')
        finally:
            admin.close()


def _comptes_globaux(index: Any) -> dict[str, int]:
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            comptes: dict[str, int] = {}
            for nom, requete in (
                ("fiche", "SELECT COUNT(*) FROM fiche"),
                ("pieces", "SELECT COUNT(*) FROM fiche_piece_jointe"),
                ("champs", "SELECT COUNT(*) FROM fiche_champ_extrait"),
            ):
                cursor.execute(requete)
                comptes[nom] = int(cursor.fetchone()[0])
    return comptes


class TestLot100InterrompuPuisRepris:
    def test_cycle_complet(self, base_lot: dict) -> None:
        index = base_lot["index"]
        archive = base_lot["archive"]
        empreintes_avant = depot.empreintes_arbre(archive)

        id_lot = depot.creer_lot(index, archive)
        etat_initial = depot.etat_lot(index, id_lot)
        assert etat_initial["nb_dossiers"] == NB_DOSSIERS
        assert len(etat_initial["restants"]) == NB_DOSSIERS

        # --- interruption volontaire à mi-parcours ---
        debut = time.perf_counter()
        etat = depot.executer_lot(index, id_lot, interrompre_apres=40)
        duree_avant_interruption = time.perf_counter() - debut
        assert etat["statut"] == "interrompu"
        assert etat["nb_traites"] + etat["nb_echecs"] == 40
        assert len(etat["restants"]) == NB_DOSSIERS - 40

        # --- reprise : le lot finit sans doublon ni dossier manquant ---
        etat = depot.executer_lot(index, id_lot)
        assert etat["statut"] == "termine"
        assert etat["nb_traites"] + etat["nb_echecs"] == NB_DOSSIERS
        assert etat["restants"] == []
        assert etat["nb_traites"] == NB_A_FICHE
        assert etat["nb_echecs"] == NB_DOSSIERS - NB_A_FICHE

        # --- les fiches sont au rendez-vous — UNE fois chacune (RG11 : les
        # 20 portants écrivent la même fiche « 7792-SO », remplacée à chaque fois) ---
        comptes = _comptes_globaux(index)
        assert comptes["fiche"] == NB_FICHES
        # pièces : 1 pièce « pièce jointe » (l'autre PDF éventuel) + fichiers annexes ;
        # les 40 dossiers à fiche n'ont qu'un PDF chacun et pas d'annexe → 0 pièce.
        assert comptes["pieces"] == 0

        # --- les échecs sont listés avec leur raison, jamais un compteur nu ---
        raisons = [d for d in etat["dossiers"] if d["statut"] == "echec"]
        assert len(raisons) == NB_DOSSIERS - NB_A_FICHE
        assert all(d["raison"] for d in raisons)
        assert any("aucun PDF" in d["raison"] for d in raisons)
        assert any("gabarit inconnu" in d["raison"] for d in raisons)

        # --- archive intacte (RG13) : empreintes avant == après ---
        assert depot.empreintes_arbre(archive) == empreintes_avant

        # --- débit mesuré → extrapolation 10 000 dossiers ---
        temps_par_dossier = duree_avant_interruption / 40
        projection = temps_par_dossier * 10_000
        minutes = projection / 60.0
        print(
            f"\nDÉBIT LOT C : {temps_par_dossier * 1000:.0f} ms/dossier "
            f"(mesuré sur 40 traitements) → 10 000 dossiers ≈ {minutes:.0f} min"
        )
        assert temps_par_dossier > 0
        # garde : l'extrapolation reste raisonnable pour un traitement de fond
        assert minutes < 24 * 60

    def test_rejeu_complet_zero_nouvelle_fiche(self, base_lot: dict, tmp_path: Path) -> None:
        """Rejouer un lot entier sur la même archive : chaque dossier est
        « déjà traité » — 0 nouvelle fiche, 0 nouveau rattachement.
        (Mini-archive interne : le test ne dépend pas de l'ordre d'exécution.)"""
        index = base_lot["index"]
        mini = tmp_path / "REJEU"
        (mini / "UN").mkdir(parents=True)
        (mini / "DEUX").mkdir()
        shutil.copy(ARCHIVE_7792, mini / "UN" / "fiche.pdf")
        (mini / "DEUX" / "vide.txt").write_text("x", encoding="utf-8")

        premier_lot = depot.creer_lot(index, mini)
        depot.executer_lot(index, premier_lot)
        avant = _comptes_globaux(index)

        id_lot2 = depot.creer_lot(index, mini, notes="rejeu complet")
        etat = depot.executer_lot(index, id_lot2)
        apres = _comptes_globaux(index)
        # les DONNÉES ne bougent pas (la traçabilité, elle, trace chaque tentative) :
        assert apres["fiche"] == avant["fiche"] and apres["pieces"] == avant["pieces"]
        assert apres["champs"] == avant["champs"], "le rejeu a créé des données"
        assert etat["nb_traites"] + etat["nb_echecs"] == 2
        raisons = [d["raison"] or "" for d in etat["dossiers"] if d["statut"] == "traite"]
        assert any("deja_traite" in r for r in raisons), "le rejeu n'a pas reconnu le dossier déjà traité"

    def test_lot_inconnu_404(self, base_lot: dict) -> None:
        with pytest.raises(DepotImpossible, match="inconnu"):
            depot.etat_lot(base_lot["index"], 999999)

    def test_racine_sans_sous_dossier_refusee(self, base_lot: dict, tmp_path: Path) -> None:
        with pytest.raises(DepotImpossible, match="Aucun sous-dossier"):
            depot.creer_lot(base_lot["index"], tmp_path)


class TestPontImportPipeline:
    def test_porte_a_via_import_pipeline(self, base_lot: dict, tmp_path: Path) -> None:
        """« Étendre, ne pas réécrire » : la porte A est exposée par le module
        historique import_pipeline (pont mince vers le dépôt Lot C)."""
        from seamtech_search import import_pipeline

        index = base_lot["index"]
        mini = tmp_path / "PONT"
        mini.mkdir()
        shutil.copy(ARCHIVE_7792, mini / "fiche.pdf")
        resultat = import_pipeline.importer_dossier_complet(index, mini)
        assert resultat["statut"] == "traite" and resultat["fiche"] == "7792-SO"
        avec_lot = import_pipeline.importer_lot_dossiers(index, mini.parent, notes="pont")
        assert avec_lot["id_lot"] > 0 and avec_lot["etat"]["statut"] == "termine"


class TestTachesDeFond:
    def test_executer_lot_en_thread_etat_consultable(self, base_lot: dict, tmp_path: Path) -> None:
        """Sans Redis : petit dossier en tâche de fond in-process, l'état du lot
        reste consultable pendant et après (progression en base)."""
        import threading

        index = base_lot["index"]
        mini = tmp_path / "MINI"
        mini.mkdir()
        (mini / "UN").mkdir()
        shutil.copy(ARCHIVE_7792, mini / "UN" / "fiche.pdf")
        (mini / "DEUX").mkdir()
        (mini / "DEUX" / "vide.txt").write_text("x", encoding="utf-8")
        id_lot = depot.creer_lot(index, mini)
        thread = threading.Thread(target=depot.executer_lot, args=(index, id_lot), daemon=True)
        thread.start()
        thread.join(timeout=60)
        etat = depot.etat_lot(index, id_lot)
        assert etat["statut"] == "termine"
        assert etat["nb_traites"] == 1 and etat["nb_echecs"] == 1
