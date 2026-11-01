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
from tests.helpers_postgres_isole import url_base_jetable

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
        # MAN-04 : l'opérateur doit lire la cause (« PDF illisible »), pas
        # seulement un score brut — le score reste cité pour le diagnostic.
        assert "PDF illisible" in plan.raison_refus and "corrompu.pdf=-1" in plan.raison_refus

    def test_dossier_absent_leve_avec_chemin(self, tmp_path: Path) -> None:
        with pytest.raises(DepotImpossible, match="introuvable"):
            scanner_dossier(tmp_path / "absent", list(GABARITS_EMBARQUES))

    def test_aucun_gabarit_actif_leve_un_refus_explicite(self, tmp_path: Path) -> None:
        """Registre vide = refus documenté (jamais un ValueError de max())."""
        dossier = tmp_path / "D0005"
        dossier.mkdir()
        shutil.copy(ARCHIVE_7792 / "fiche-7792-SO_ffab.pdf", dossier / "fiche.pdf")
        with pytest.raises(DepotImpossible, match="aucun gabarit actif"):
            scanner_dossier(dossier, [])

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
    url_base = url_base_jetable(URL_PG, nom_base)
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
        # A02 (audit du 2026-10-08) : le dépôt appelle désormais
        # ``ecrire_fiche_resultat`` (elle publie la révision). Le point
        # d'interception suit le symbole réellement appelé — la garantie
        # testée, elle, ne change pas : un crash APRÈS l'écriture ne laisse rien.
        vraie_ecriture = depot.ecrire_fiche_resultat

        def ecrire_puis_crash(idx: Any, fiche: Any, connexion: Any = None, **_kwargs: Any) -> Any:
            vraie_ecriture(idx, fiche, connexion=connexion)
            raise RuntimeError("crash simulé après l'écriture de la fiche")

        monkeypatch.setattr(depot, "ecrire_fiche_resultat", ecrire_puis_crash)
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
        assert statut == "echec" and raison == resultat["raison"]
        assert "crash simulé" not in raison

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

    def test_rejeu_televerse_dans_une_nouvelle_session_ne_duplique_rien(
        self, base_depot: dict, tmp_path: Path
    ) -> None:
        """MAN-02 (campagne manuelle Lot 10) : re-téléverser le MÊME dossier
        crée une nouvelle session (nouveau jeton, donc un autre chemin absolu
        de staging) — le rejeu doit rester sans effet, comme l'annonce l'écran
        (« rejouer un dossier ne crée rien »)."""
        index = base_depot["index"]

        def dossier_stage(jeton: str) -> Path:
            dossier = (
                tmp_path
                / "uploads"
                / "lots-televerses"
                / jeton
                / "fichiers"
                / "ANNEE-DEMO"
                / "AFFAIRE-STAGEE"
            )
            dossier.mkdir(parents=True)
            shutil.copy(ARCHIVE_7792 / "fiche-7792-SO_ffab.pdf", dossier / "fiche-7792-SO_ffab.pdf")
            (dossier / "croquis-atelier.txt").write_text("repère des renforts", encoding="utf-8")
            return dossier

        premier = depot.deposer_dossier(index, dossier_stage("jeton-session-A"))
        assert premier["statut"] == "traite"
        avant = _comptes(index)
        second = depot.deposer_dossier(index, dossier_stage("jeton-session-B"))
        apres = _comptes(index)
        assert second["statut"] == "deja_traite", second
        assert apres["fiche"] == avant["fiche"] and apres["pieces"] == avant["pieces"]
        assert apres["champs"] == avant["champs"], "le rejeu téléversé a dupliqué des données"
        # Le PDF principal reste ouvrable depuis la fiche : la clé stockée
        # garde son format chemin absolu, lu par pieces_de_fiche/approbations.
        from seamtech_search.fiches.routes import pieces_de_fiche

        pieces = pieces_de_fiche(index, "7792-SO")["pieces"]
        assert any(p["is_primary_pdf"] for p in pieces), pieces

    def test_pdf_televerse_modifie_meme_chemin_ne_duplique_pas_les_annexes(
        self, base_depot: dict, tmp_path: Path
    ) -> None:
        """Le même dossier envoyé dans une autre session avec un PDF modifié
        remplace la fiche, mais une annexe inchangée reste une seule pièce.

        Sans identité logique des pièces stagées, le nouveau jeton crée un
        second document et un second lien pour la même annexe : l'écran
        afficherait deux fois le même fichier.
        """
        index = base_depot["index"]
        source_pdf = (ARCHIVE_7792 / "fiche-7792-SO_ffab.pdf").read_bytes()

        def dossier_stage(jeton: str, pdf_octets: bytes, annexe: str) -> Path:
            dossier = (
                tmp_path
                / "uploads"
                / "lots-televerses"
                / jeton
                / "fichiers"
                / "ANNEE-MODIFIEE"
                / "AFFAIRE-STAGEE"
            )
            dossier.mkdir(parents=True)
            (dossier / "fiche-7792-SO_ffab.pdf").write_bytes(pdf_octets)
            (dossier / "annexe-suivi-modifications.txt").write_text(annexe, encoding="utf-8")
            return dossier

        premier = depot.deposer_dossier(
            index,
            dossier_stage("jeton-modifie-A", source_pdf, "repère des renforts"),
        )
        assert premier["statut"] == "traite", premier
        avant = _comptes(index)

        pdf_modifie = source_pdf + b"\n% variante synthetique meme chemin logique\n"
        second = depot.deposer_dossier(
            index,
            dossier_stage("jeton-modifie-B", pdf_modifie, "repère des renforts"),
        )
        apres = _comptes(index)
        assert second["statut"] == "traite", second
        assert apres["fiche"] == avant["fiche"]
        assert apres["pieces"] == avant["pieces"], "une annexe inchangée a été dupliquée"

        from seamtech_search.fiches.routes import pieces_de_fiche

        pieces = pieces_de_fiche(index, "7792-SO")["pieces"]
        noms = [piece["name"] for piece in pieces]
        assert noms.count("annexe-suivi-modifications.txt") == 1, noms
        principal = next(piece for piece in pieces if piece["is_primary_pdf"])
        assert principal["size"] == len(pdf_modifie)

        troisieme = depot.deposer_dossier(
            index,
            dossier_stage(
                "jeton-modifie-C",
                pdf_modifie + b"% seconde variante synthetique\n",
                "repère des renforts, version 2",
            ),
        )
        assert troisieme["statut"] == "traite", troisieme
        assert _comptes(index)["pieces"] == avant["pieces"], (
            "une annexe modifiée au même chemin logique doit remplacer son lien"
        )
        pieces = pieces_de_fiche(index, "7792-SO")["pieces"]
        annexes = [piece for piece in pieces if piece["name"] == "annexe-suivi-modifications.txt"]
        assert len(annexes) == 1, pieces
        assert annexes[0]["size"] == len("repère des renforts, version 2".encode("utf-8"))

    def test_rejeu_televerse_reconnait_une_ancienne_cle_absolue(
        self, base_depot: dict, tmp_path: Path
    ) -> None:
        """Une base alimentée AVANT la clé logique porte des clés absolues
        stagées (jeton inclus) : le premier rejeu après mise à jour doit lui
        aussi être reconnu, sinon il duplique une dernière fois."""
        index = base_depot["index"]

        def dossier_stage(jeton: str) -> Path:
            dossier = (
                tmp_path
                / "uploads"
                / "lots-televerses"
                / jeton
                / "fichiers"
                / "ANNEE-ANCIENNE-CLE"
                / "AFFAIRE-STAGEE"
            )
            dossier.mkdir(parents=True)
            shutil.copy(ARCHIVE_7792 / "fiche-7792-SO_ffab.pdf", dossier / "fiche-7792-SO_ffab.pdf")
            (dossier / "croquis-atelier.txt").write_text("repère des renforts", encoding="utf-8")
            return dossier

        dossier_a = dossier_stage("jeton-ancien-A")
        premier = depot.deposer_dossier(index, dossier_a)
        assert premier["statut"] == "traite"
        # Réécrire la clé sous son ANCIENNE forme absolue (pré-correctif).
        pdf_a = dossier_a / "fiche-7792-SO_ffab.pdf"
        ancienne_cle = (
            os.path.normcase(str(pdf_a.resolve())) + "|" + depot.empreinte_fichier(pdf_a)
        )
        id_ligne = depot.etat_lot(index, premier["id_lot"])["dossiers"][0]["id_lot_dossier"]
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    "UPDATE lot_dossier SET cle_idempotence = %s WHERE id_lot_dossier = %s",
                    (ancienne_cle, id_ligne),
                )
        avant = _comptes(index)
        second = depot.deposer_dossier(index, dossier_stage("jeton-ancien-B"))
        apres = _comptes(index)
        assert second["statut"] == "deja_traite", second
        assert apres["pieces"] == avant["pieces"] and apres["champs"] == avant["champs"]

    def test_cle_idempotence_normcase_plus_empreinte(self, dossier_ok: Path) -> None:
        empreinte = depot.empreinte_fichier(dossier_ok / "fiche-7792-SO_ffab.pdf")
        cle = depot.cle_idempotence(dossier_ok / "fiche-7792-SO_ffab.pdf", empreinte)
        # même normalisation que le crawler (os.path.normcase) + SHA-256
        assert cle == os.path.normcase(str((dossier_ok / "fiche-7792-SO_ffab.pdf").resolve())) + "|" + empreinte
        # une empreinte différente (fiche corrigée) donne une clé différente
        autre = depot.cle_idempotence(dossier_ok / "fiche-7792-SO_ffab.pdf", "0" * 64)
        assert autre != cle
        # Chemins stagés (Lot 10) : le format STOCKÉ reste le chemin absolu
        # (des lecteurs retrouvent le PDF par cette clé) — la reconnaissance
        # inter-sessions est prouvée par les tests de rejeu ci-dessus.
        stage = Path("/data/uploads/lots-televerses/jeton-A/fichiers/ANNEE/AFFAIRE/fiche.pdf")
        assert depot._chemin_logique_televersement(stage) == "ANNEE/AFFAIRE/fiche.pdf"
        assert depot._chemin_logique_televersement(dossier_ok / "fiche-7792-SO_ffab.pdf") is None

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


class TestObjetsPromus:
    def test_depot_lie_pdf_et_annexe_dans_transaction(self, base_depot: dict, dossier_ok: Path) -> None:
        index = base_depot['index']
        fichiers = sorted(p for p in dossier_ok.iterdir() if p.is_file())
        promotion = {str(p.resolve()): {'object_key': f'objets/{i}', 'object_bucket': 'bucket-test',
                                        'sha256': depot.empreinte_fichier(p)} for i, p in enumerate(fichiers)}
        resultat = depot.deposer_dossier(index, dossier_ok, objets_promus=promotion)
        assert resultat['statut'] == 'traite'
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute('SELECT path, object_key, object_bucket, upload_status FROM documents WHERE path = ANY(%s)',
                               ([str(p) for p in fichiers],))
                lignes = cursor.fetchall()
        assert len(lignes) == len(fichiers)
        for path, cle, bucket, statut in lignes:
            assert (cle, bucket, statut) == (promotion[str(Path(path).resolve())]['object_key'], 'bucket-test', 'uploaded')

    def test_promotion_manquante_annule_fiche_et_documents(self, base_depot: dict, dossier_ok: Path) -> None:
        index = base_depot['index']
        pdf = next(dossier_ok.glob('*.pdf'))
        promotion = {str(pdf.resolve()): {'object_key': 'objets/pdf', 'object_bucket': 'bucket-test',
                                         'sha256': depot.empreinte_fichier(pdf)}}
        avant = _comptes(index)
        resultat = depot.deposer_dossier(index, dossier_ok, objets_promus=promotion)
        assert resultat['statut'] == 'echec'
        apres = _comptes(index)
        assert apres['fiche'] == avant['fiche']
        assert apres['pieces'] == avant['pieces']


def test_lot_objets_promus_lie_les_documents(base_depot: dict, dossier_ok: Path) -> None:
    index = base_depot['index']
    promotion = {str(p.resolve()): {'object_key': f'lot/{i}', 'object_bucket': 'bucket-test',
                                   'sha256': depot.empreinte_fichier(p)}
                 for i, p in enumerate(sorted(dossier_ok.iterdir())) if p.is_file()}
    lot = depot.creer_lot(index, dossier_ok)
    resultat = depot.executer_lot(index, lot, objets_promus=promotion)
    assert resultat['nb_echecs'] == 0
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT object_key, object_bucket FROM documents WHERE path = ANY(%s)',
                            (list(promotion),))
            lignes = curseur.fetchall()
    assert len(lignes) == len(promotion)
    assert all(cle and bucket == 'bucket-test' for cle, bucket in lignes)


def test_lot_televerse_promeut_et_rattache_les_documents(base_depot: dict, tmp_path: Path) -> None:
    from io import BytesIO
    from types import SimpleNamespace

    from seamtech_search.fiches import televersement_lot as t
    from tests.test_promotion_lot_objets import StockageSimule

    config = SimpleNamespace(database_path=tmp_path / 'data' / 'search.db', max_file_size_bytes=1024 * 1024,
                             min_free_bytes=0, storage_backend='s3')
    fichiers = {f'{tmp_path.name}/fiche.pdf': (ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf').read_bytes(),
                f'{tmp_path.name}/annexe.txt': b'piece synthetique'}
    token = t.creer_session_televersement_lot(config, nom_racine='SYNTHETIQUE', nombre_fichiers=len(fichiers),
                                               taille_totale_octets=sum(map(len, fichiers.values())))['token']
    t.recevoir_fichiers_televersement_lot(config, token, chemins_relatives=list(fichiers),
                                         flux=[BytesIO(data) for data in fichiers.values()])
    t.terminer_session_televersement_lot(config, token)
    index = base_depot['index']
    creation = t.creer_lot_session_televersement(config, token, creer_lot=lambda root: depot.creer_lot(index, root))
    client = StockageSimule()
    resultat = depot.executer_lot(index, creation['id_lot'], config=config, client_documents=client)
    assert resultat['nb_echecs'] == 0
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            chemins = [str((creation['racine'] / n).resolve()) for n in fichiers]
            curseur.execute('SELECT path, object_key, object_bucket, upload_status FROM documents WHERE path = ANY(%s)',
                            (chemins,))
            lignes = curseur.fetchall()
    assert len(lignes) == len(fichiers)
    for chemin, key, bucket, statut in lignes:
        assert bucket == client.bucket_name and statut == 'uploaded'
        assert client.objets[key] == Path(chemin).read_bytes()


def test_worker_lot_televerse_promeut_avant_le_depot(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from io import BytesIO
    from types import SimpleNamespace

    from seamtech_search import storage
    from seamtech_search.fiches import televersement_lot as t
    from seamtech_search.jobs import create_job
    from seamtech_search.worker import _process_lot_task
    from tests.test_promotion_lot_objets import StockageSimule

    config = SimpleNamespace(database_path=tmp_path / 'data' / 'search.db', max_file_size_bytes=1024 * 1024,
                             min_free_bytes=0, storage_backend='s3')
    fichiers = {f'{tmp_path.name}/fiche.pdf': (ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf').read_bytes(),
                f'{tmp_path.name}/annexe.txt': b'piece synthetique'}
    token = t.creer_session_televersement_lot(config, nom_racine='SYNTHETIQUE', nombre_fichiers=len(fichiers),
                                               taille_totale_octets=sum(map(len, fichiers.values())))['token']
    t.recevoir_fichiers_televersement_lot(config, token, chemins_relatives=list(fichiers),
                                         flux=[BytesIO(data) for data in fichiers.values()])
    t.terminer_session_televersement_lot(config, token)
    index = base_depot['index']
    creation = t.creer_lot_session_televersement(config, token, creer_lot=lambda root: depot.creer_lot(index, root))
    client = StockageSimule()
    monkeypatch.setattr(storage, 'S3StorageClient', lambda **kwargs: client)
    job_id = f"lot-{creation['id_lot']}"
    create_job(index, job_id, str(creation['racine']), durability='process_memory')
    resultat = _process_lot_task({'job_id': job_id, 'id_lot': creation['id_lot']}, config, index, None,
                                 worker_id=None, heartbeat=None)
    assert resultat['status'] == 'completed'
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT path, object_key FROM documents WHERE path = ANY(%s)',
                            ([str((creation['racine'] / n).resolve()) for n in fichiers],))
            lignes = curseur.fetchall()
    assert len(lignes) == len(fichiers)
    assert all(client.objets[cle] == Path(chemin).read_bytes() for chemin, cle in lignes)


def test_rejeu_televerse_promeut_documents_anciens(base_depot: dict, tmp_path: Path) -> None:
    from io import BytesIO
    from types import SimpleNamespace

    from seamtech_search.fiches import televersement_lot as t
    from tests.test_promotion_lot_objets import StockageSimule

    config = SimpleNamespace(database_path=tmp_path / 'data' / 'search.db', max_file_size_bytes=1024 * 1024,
                             min_free_bytes=0, storage_backend='s3')
    fichiers = {f'{tmp_path.name}/fiche.pdf': (ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf').read_bytes(),
                f'{tmp_path.name}/annexe.txt': b'piece synthetique'}
    index = base_depot['index']
    def creer():
        token = t.creer_session_televersement_lot(config, nom_racine='SYNTHETIQUE', nombre_fichiers=len(fichiers),
                                                   taille_totale_octets=sum(map(len, fichiers.values())))['token']
        t.recevoir_fichiers_televersement_lot(config, token, chemins_relatives=list(fichiers),
                                             flux=[BytesIO(data) for data in fichiers.values()])
        t.terminer_session_televersement_lot(config, token)
        return t.creer_lot_session_televersement(config, token, creer_lot=lambda root: depot.creer_lot(index, root))
    ancien = creer()
    assert depot.executer_lot(index, ancien['id_lot'])['nb_echecs'] == 0
    nouveau = creer()
    client = StockageSimule()
    assert depot.executer_lot(index, nouveau['id_lot'], config=config, client_documents=client)['nb_echecs'] == 0
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT path, object_key FROM documents WHERE path = ANY(%s)',
                            ([str((ancien['racine'] / n).resolve()) for n in fichiers],))
            lignes = curseur.fetchall()
    assert len(lignes) == len(fichiers)
    assert all(key and client.objets[key] == Path(chemin).read_bytes() for chemin, key in lignes)
    fichiers[f'{tmp_path.name}/annexe.txt'] = b'annexe modifiee'
    modifie = creer()
    assert depot.executer_lot(index, modifie['id_lot'], config=config, client_documents=client)['nb_echecs'] == 0
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT path, object_key FROM documents WHERE path = ANY(%s)',
                            ([str((ancien['racine'] / n).resolve()) for n in fichiers],))
            assert sorted(curseur.fetchall()) == sorted(lignes)
            nom_annexe = f'{tmp_path.name}/annexe.txt'
            curseur.execute('SELECT object_key FROM documents WHERE path = %s', (str(modifie['racine'] / nom_annexe),))
            assert client.objets[curseur.fetchone()[0]] == fichiers[nom_annexe]


def test_racine_session_remplacee_par_lien_externe_refusee(base_depot: dict, tmp_path: Path) -> None:
    from io import BytesIO
    from types import SimpleNamespace

    from seamtech_search.fiches import televersement_lot as t
    from tests.test_promotion_lot_objets import StockageSimule

    config = SimpleNamespace(database_path=tmp_path / 'data' / 'search.db', max_file_size_bytes=1024 * 1024,
                             min_free_bytes=0, storage_backend='s3')
    pdf = (ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf').read_bytes()
    token = t.creer_session_televersement_lot(config, nom_racine='TEST', nombre_fichiers=1,
                                               taille_totale_octets=len(pdf))['token']
    t.recevoir_fichiers_televersement_lot(config, token, chemins_relatives=['dossier/fiche.pdf'], flux=[BytesIO(pdf)])
    t.terminer_session_televersement_lot(config, token)
    index = base_depot['index']
    creation = t.creer_lot_session_televersement(config, token, creer_lot=lambda root: depot.creer_lot(index, root))
    racine = creation['racine']
    externe = tmp_path / 'externe'
    racine.rename(externe)
    racine.symlink_to(externe, target_is_directory=True)
    client = StockageSimule()
    with pytest.raises(DepotImpossible, match='session'):
        depot.executer_lot(index, creation['id_lot'], config=config, client_documents=client)
    assert client.envois == 0
    assert (externe / 'dossier' / 'fiche.pdf').read_bytes() == pdf


def test_annulation_pendant_promotion_est_annulee_et_reprenable(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from io import BytesIO
    from types import SimpleNamespace

    from seamtech_search import jobs, storage
    from seamtech_search.fiches import televersement_lot as t
    from seamtech_search.worker import _process_lot_task
    from tests.test_promotion_lot_objets import StockageSimule

    config = SimpleNamespace(database_path=tmp_path / 'data' / 'search.db', max_file_size_bytes=1024 * 1024,
                             min_free_bytes=0, storage_backend='s3')
    fichiers = {'annulation/fiche.pdf': (ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf').read_bytes(),
                'annulation/annexe.txt': b'annexe synthetique'}
    token = t.creer_session_televersement_lot(config, nom_racine='TEST', nombre_fichiers=2,
                                               taille_totale_octets=sum(map(len, fichiers.values())))['token']
    t.recevoir_fichiers_televersement_lot(config, token, chemins_relatives=list(fichiers),
                                         flux=[BytesIO(data) for data in fichiers.values()])
    t.terminer_session_televersement_lot(config, token)
    index = base_depot['index']
    creation = t.creer_lot_session_televersement(config, token, creer_lot=lambda root: depot.creer_lot(index, root))
    class ClientAnnulation(StockageSimule):
        def upload_file(self, *args, **kwargs):
            cle = super().upload_file(*args, **kwargs)
            if self.activer_annulation:
                self.annule = True
            return cle
    client = ClientAnnulation()
    client.annule = False
    client.activer_annulation = True
    monkeypatch.setattr(storage, 'S3StorageClient', lambda **kwargs: client)
    monkeypatch.setattr(jobs, 'is_job_cancelled', lambda *_args: client.annule)
    job_id = f"lot-{creation['id_lot']}"
    jobs.create_job(index, job_id, str(creation['racine']), durability='process_memory')
    resultat = _process_lot_task({'job_id': job_id, 'id_lot': creation['id_lot']}, config, index,
                                 SimpleNamespace(is_configured=lambda: False), worker_id=None, heartbeat=None)
    assert resultat['status'] == 'cancelled'
    assert depot.etat_lot(index, creation['id_lot'])['statut'] == 'annule'
    assert all((creation['racine'] / nom).read_bytes() == data for nom, data in fichiers.items())
    assert client.envois == 1
    client.activer_annulation = False
    assert depot.executer_lot(index, creation['id_lot'], config=config, client_documents=client)['nb_echecs'] == 0
    assert client.envois == 2


def test_worker_perd_claim_pendant_promotion_sans_marquer_lot_annule(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from io import BytesIO
    from types import SimpleNamespace

    from seamtech_search import jobs, storage
    from seamtech_search.fiches import televersement_lot as t
    from seamtech_search.worker import _process_lot_task
    from tests.test_promotion_lot_objets import StockageSimule

    config = SimpleNamespace(database_path=tmp_path / 'data' / 'search.db', max_file_size_bytes=1024 * 1024,
                             min_free_bytes=0, storage_backend='s3')
    fichiers = {'claim/fiche.pdf': (ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf').read_bytes(),
                'claim/annexe.txt': b'annexe synthetique'}
    token = t.creer_session_televersement_lot(config, nom_racine='TEST', nombre_fichiers=2,
                                               taille_totale_octets=sum(map(len, fichiers.values())))['token']
    t.recevoir_fichiers_televersement_lot(config, token, chemins_relatives=list(fichiers),
                                         flux=[BytesIO(data) for data in fichiers.values()])
    t.terminer_session_televersement_lot(config, token)
    index = base_depot['index']
    creation = t.creer_lot_session_televersement(config, token, creer_lot=lambda root: depot.creer_lot(index, root))
    job_id = f"lot-{creation['id_lot']}"
    jobs.create_job(index, job_id, str(creation['racine']), durability='durable')
    class ClientTransfert(StockageSimule):
        def upload_file(self, *args, **kwargs):
            cle = super().upload_file(*args, **kwargs)
            with index.connect() as connexion:
                with connexion.cursor() as cursor:
                    cursor.execute("UPDATE import_jobs SET claimed_by = %s WHERE id = %s", ('worker-b', job_id))
            return cle
    client = ClientTransfert()
    monkeypatch.setattr(storage, 'S3StorageClient', lambda **kwargs: client)
    resultat = _process_lot_task({'job_id': job_id, 'id_lot': creation['id_lot']}, config, index, None,
                                 worker_id='worker-a', heartbeat=None)
    assert resultat['status'] == 'stale_worker'
    assert depot.etat_lot(index, creation['id_lot'])['statut'] != 'annule'
    assert jobs.get_job(index, job_id)['claimed_by'] == 'worker-b'
    assert client.envois == 1


def test_perte_claim_redis_pendant_promotion_stoppe_worker(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    from io import BytesIO
    from types import SimpleNamespace

    from seamtech_search import jobs, storage
    from seamtech_search.fiches import televersement_lot as t
    from seamtech_search.redis_store import RedisStore
    from seamtech_search.worker import _process_lot_task
    from tests.test_promotion_lot_objets import StockageSimule

    url = os.environ.get('SEAMTECH_TEST_REDIS_URL')
    if not url:
        pytest.skip('Redis de test requis')
    magasin = RedisStore(redis_url=request.getfixturevalue('redis_isole'))
    config = SimpleNamespace(database_path=tmp_path / 'data' / 'search.db', max_file_size_bytes=1024 * 1024,
                             min_free_bytes=0, storage_backend='s3')
    fichiers = {'redis-claim/fiche.pdf': (ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf').read_bytes(),
                'redis-claim/annexe.txt': b'annexe synthetique'}
    token = t.creer_session_televersement_lot(config, nom_racine='TEST', nombre_fichiers=2,
                                               taille_totale_octets=sum(map(len, fichiers.values())))['token']
    t.recevoir_fichiers_televersement_lot(config, token, chemins_relatives=list(fichiers),
                                         flux=[BytesIO(data) for data in fichiers.values()])
    t.terminer_session_televersement_lot(config, token)
    index = base_depot['index']
    creation = t.creer_lot_session_televersement(config, token, creer_lot=lambda root: depot.creer_lot(index, root))
    job_id = f"lot-{creation['id_lot']}-redis-fencing"
    jobs.create_job(index, job_id, str(creation['racine']), durability='durable')
    assert magasin.revendiquer_tache('imports', job_id, 'worker-a')
    class ClientPerte(StockageSimule):
        def upload_file(self, *args, **kwargs):
            cle = super().upload_file(*args, **kwargs)
            assert magasin.liberer_revendication('imports', job_id, 'worker-a') == 1
            assert magasin.revendiquer_tache('imports', job_id, 'worker-b')
            return cle
    client = ClientPerte()
    monkeypatch.setattr(storage, 'S3StorageClient', lambda **kwargs: client)
    try:
        resultat = _process_lot_task({'job_id': job_id, 'id_lot': creation['id_lot']}, config, index, magasin,
                                     worker_id='worker-a', heartbeat=None)
        assert resultat['status'] == 'stale_worker'
        assert magasin.revendication_appartient_a('imports', job_id, 'worker-b')
        assert depot.etat_lot(index, creation['id_lot'])['statut'] != 'annule'
        assert client.envois == 1
    finally:
        magasin.liberer_revendication('imports', job_id, 'worker-b')


def test_perte_claim_juste_avant_publication_n_annonce_pas_succes(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from seamtech_search import jobs
    from seamtech_search.worker import _process_lot_task

    index = base_depot['index']
    job_id = 'lot-test-perte-avant-publication'
    jobs.create_job(index, job_id, str(tmp_path), durability='durable')
    def executer(*args, **kwargs):
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("UPDATE import_jobs SET claimed_by = %s WHERE id = %s", ('worker-b', job_id))
        return {'nb_echecs': 0}
    monkeypatch.setattr(depot, 'executer_lot', executer)
    resultat = _process_lot_task({'job_id': job_id, 'id_lot': 1}, SimpleNamespace(), index, None,
                                 worker_id='worker-a', heartbeat=None)
    assert resultat['status'] == 'stale_worker'
    assert jobs.get_job(index, job_id)['claimed_by'] == 'worker-b'


def test_perte_claim_pendant_scan_ne_persiste_ni_fiche_ni_echec(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from seamtech_search import jobs
    from seamtech_search.worker import _process_lot_task

    index = base_depot['index']
    racine = tmp_path / 'archive'
    dossier = racine / 'synthese'
    dossier.mkdir(parents=True)
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', dossier / 'fiche.pdf')
    id_lot = depot.creer_lot(index, racine)
    job_id = f'lot-{id_lot}-transfert-scan'
    jobs.create_job(index, job_id, str(racine), durability='durable')
    scanner_original = depot.scanner_dossier
    def scanner_et_transfert(*args, **kwargs):
        plan = scanner_original(*args, **kwargs)
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute('UPDATE import_jobs SET claimed_by = %s WHERE id = %s', ('worker-b', job_id))
        return plan
    monkeypatch.setattr(depot, 'scanner_dossier', scanner_et_transfert)
    avant = _comptes(index)
    resultat = _process_lot_task({'job_id': job_id, 'id_lot': id_lot},
                                 SimpleNamespace(database_path=tmp_path / 'data' / 'search.db', storage_backend='local'),
                                 index, None, worker_id='worker-a', heartbeat=None)
    assert resultat['status'] == 'stale_worker'
    assert _comptes(index) == avant
    etat = depot.etat_lot(index, id_lot)
    assert etat['dossiers'][0]['statut'] == 'en_attente'


def test_transaction_depot_verrouille_claim_pendant_ecriture(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    import psycopg2

    from seamtech_search import jobs
    from seamtech_search.worker import _process_lot_task

    index = base_depot['index']
    racine = tmp_path / 'archive'
    dossier = racine / 'synthese'
    dossier.mkdir(parents=True)
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', dossier / 'fiche.pdf')
    id_lot = depot.creer_lot(index, racine)
    job_id = f'lot-{id_lot}-verrou-ecriture'
    jobs.create_job(index, job_id, str(racine), durability='durable')
    original = depot.ecrire_fiche_resultat
    blocages = []
    def essayer_transfert(*args, **kwargs):
        autre = psycopg2.connect(base_depot['url'])
        try:
            with autre.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '100ms'")
                try:
                    cursor.execute('UPDATE import_jobs SET claimed_by = %s WHERE id = %s', ('worker-b', job_id))
                except psycopg2.errors.LockNotAvailable:
                    blocages.append(True)
                else:
                    autre.commit()
        finally:
            autre.close()
        return original(*args, **kwargs)
    monkeypatch.setattr(depot, 'ecrire_fiche_resultat', essayer_transfert)
    resultat = _process_lot_task({'job_id': job_id, 'id_lot': id_lot},
                                 SimpleNamespace(database_path=tmp_path / 'data' / 'search.db', storage_backend='local'),
                                 index, None, worker_id='worker-a', heartbeat=None)
    assert blocages == [True]
    assert resultat['status'] == 'completed'


def test_annulation_lot_par_worker_dechu_est_refusee(base_depot: dict, tmp_path: Path) -> None:
    from seamtech_search import jobs

    index = base_depot['index']
    racine = tmp_path / 'archive'
    dossier = racine / 'synthese'
    dossier.mkdir(parents=True)
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', dossier / 'fiche.pdf')
    id_lot = depot.creer_lot(index, racine)
    job_id = f'lot-{id_lot}-annulation-fencing'
    jobs.create_job(index, job_id, str(racine), durability='durable')
    assert jobs.acquerir_claim_job(index, job_id, 'worker-b')
    with pytest.raises(depot.DepotClaimPerdu):
        depot.annuler_lot(index, id_lot, claim=(job_id, 'worker-a'))
    assert depot.etat_lot(index, id_lot)['statut'] != 'annule'
    assert depot.annuler_lot(index, id_lot, claim=(job_id, 'worker-b'))['annule']


def test_worker_dechu_avant_annulation_ne_modifie_pas_le_lot(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from seamtech_search import jobs
    from seamtech_search.worker import _process_lot_task

    index = base_depot['index']
    racine = tmp_path / 'archive'
    dossier = racine / 'synthese'
    dossier.mkdir(parents=True)
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', dossier / 'fiche.pdf')
    id_lot = depot.creer_lot(index, racine)
    job_id = f'lot-{id_lot}-transfert-annulation'
    jobs.create_job(index, job_id, str(racine), durability='durable')
    monkeypatch.setattr(depot, 'executer_lot', lambda *args, **kwargs: {'annule': True})
    original = depot.annuler_lot
    def transferer_avant_annulation(index_, numero, **kwargs):
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute('UPDATE import_jobs SET claimed_by = %s WHERE id = %s', ('worker-b', job_id))
        return original(index_, numero, **kwargs)
    monkeypatch.setattr(depot, 'annuler_lot', transferer_avant_annulation)
    resultat = _process_lot_task({'job_id': job_id, 'id_lot': id_lot}, SimpleNamespace(), index, None,
                                 worker_id='worker-a', heartbeat=None)
    assert resultat['status'] == 'stale_worker'
    assert depot.etat_lot(index, id_lot)['statut'] != 'annule'
    assert jobs.get_job(index, job_id)['claimed_by'] == 'worker-b'


def test_panne_mise_a_jour_ligne_annule_aussi_fiche(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg2

    index = base_depot['index']
    racine = tmp_path / 'archive'
    dossier = racine / 'synthese'
    dossier.mkdir(parents=True)
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', dossier / 'fiche.pdf')
    id_lot = depot.creer_lot(index, racine)
    avant = _comptes(index)
    monkeypatch.setattr(depot, '_SQL_LIGNE_MAJ', 'SELECT colonne_inexistante FROM table_inexistante WHERE %s = %s AND %s = %s AND %s = %s')
    with pytest.raises(psycopg2.errors.UndefinedTable):
        depot.executer_lot(index, id_lot)
    assert _comptes(index) == avant
    assert depot.etat_lot(index, id_lot)['dossiers'][0]['statut'] == 'en_attente'


def test_erreur_apres_transfert_claim_ne_publie_pas_echec(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from seamtech_search import jobs
    from seamtech_search.worker import _process_lot_task

    index = base_depot['index']
    job_id = 'lot-test-transfert-sur-erreur'
    jobs.create_job(index, job_id, str(tmp_path), durability='durable')
    def echec_apres_transfert(*args, **kwargs):
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute('UPDATE import_jobs SET claimed_by = %s WHERE id = %s', ('worker-b', job_id))
        raise depot.DepotImpossible('panne')
    monkeypatch.setattr(depot, 'executer_lot', echec_apres_transfert)
    resultat = _process_lot_task({'job_id': job_id, 'id_lot': 1}, SimpleNamespace(), index, None,
                                 worker_id='worker-a', heartbeat=None)
    assert resultat['status'] == 'stale_worker'
    assert jobs.get_job(index, job_id)['claimed_by'] == 'worker-b'


def test_echec_enregistrement_annulation_ne_declare_pas_annule(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from seamtech_search import jobs
    from seamtech_search.worker import _process_lot_task

    index = base_depot['index']
    job_id = 'lot-test-panne-annulation'
    jobs.create_job(index, job_id, str(tmp_path), durability='durable')
    monkeypatch.setattr(depot, 'executer_lot', lambda *args, **kwargs: {'annule': True})
    def panne(*args, **kwargs):
        raise RuntimeError('panne synthetique')
    monkeypatch.setattr(depot, 'annuler_lot', panne)
    resultat = _process_lot_task({'job_id': job_id, 'id_lot': 1}, SimpleNamespace(), index, None,
                                 worker_id='worker-a', heartbeat=None)
    assert resultat['status'] == 'failed'
    job = jobs.get_job(index, job_id)
    assert job['status'] == 'running' and job['stage'] == 'failed'
    assert job['claimed_by'] == 'worker-a' and 'annulation impossible' in job['error']


def test_transfert_au_moment_fermeture_ne_retourne_pas_succes(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from seamtech_search import jobs, worker

    index = base_depot['index']
    job_id = 'lot-test-transfert-fermeture'
    jobs.create_job(index, job_id, str(tmp_path), durability='durable')
    monkeypatch.setattr(depot, 'executer_lot', lambda *args, **kwargs: {'nb_echecs': 0})
    original = worker.terminer_job
    def transfert(*args, **kwargs):
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute('UPDATE import_jobs SET claimed_by = %s WHERE id = %s', ('worker-b', job_id))
        return original(*args, **kwargs)
    monkeypatch.setattr(worker, 'terminer_job', transfert)
    resultat = worker._process_lot_task({'job_id': job_id, 'id_lot': 1}, SimpleNamespace(), index, None,
                                       worker_id='worker-a', heartbeat=None)
    assert resultat['status'] == 'stale_worker'
    job = jobs.get_job(index, job_id)
    assert job['claimed_by'] == 'worker-b'
    assert job['status'] != 'completed'


def test_perte_claim_redis_pendant_transaction_annule_fiche(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    from types import SimpleNamespace

    from seamtech_search import jobs
    from seamtech_search.redis_store import RedisStore
    from seamtech_search.worker import _process_lot_task

    url = os.environ.get('SEAMTECH_TEST_REDIS_URL')
    if not url:
        pytest.skip('Redis de test requis')
    magasin = RedisStore(redis_url=request.getfixturevalue('redis_isole'))
    index = base_depot['index']
    racine = tmp_path / 'archive'
    dossier = racine / 'synthese'
    dossier.mkdir(parents=True)
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', dossier / 'fiche.pdf')
    id_lot = depot.creer_lot(index, racine)
    job_id = f'lot-{id_lot}-claim-redis-transaction'
    jobs.create_job(index, job_id, str(racine), durability='durable')
    assert magasin.revendiquer_tache('imports', job_id, 'worker-a')
    original = depot.ecrire_fiche_resultat
    def perdre_claim(*args, **kwargs):
        ecriture = original(*args, **kwargs)
        assert magasin.liberer_revendication('imports', job_id, 'worker-a') == 1
        assert magasin.revendiquer_tache('imports', job_id, 'worker-b')
        return ecriture
    monkeypatch.setattr(depot, 'ecrire_fiche_resultat', perdre_claim)
    avant = _comptes(index)
    try:
        resultat = _process_lot_task({'job_id': job_id, 'id_lot': id_lot},
                                     SimpleNamespace(database_path=tmp_path / 'data' / 'search.db', storage_backend='local'),
                                     index, magasin, worker_id='worker-a', heartbeat=None)
        assert resultat['status'] == 'stale_worker'
        assert _comptes(index) == avant
        assert depot.etat_lot(index, id_lot)['dossiers'][0]['statut'] == 'en_attente'
    finally:
        magasin.liberer_revendication('imports', job_id, 'worker-b')


def test_panne_suivi_rejeu_rollback_liaisons_objet(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from io import BytesIO
    from types import SimpleNamespace

    import psycopg2

    from seamtech_search.fiches import televersement_lot as t
    from tests.test_promotion_lot_objets import StockageSimule

    config = SimpleNamespace(database_path=tmp_path / 'data' / 'search.db', max_file_size_bytes=1024 * 1024,
                             min_free_bytes=0, storage_backend='s3')
    pdf = (ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf').read_bytes()
    index = base_depot['index']
    def creer():
        token = t.creer_session_televersement_lot(config, nom_racine='TEST', nombre_fichiers=1,
                                                   taille_totale_octets=len(pdf))['token']
        t.recevoir_fichiers_televersement_lot(config, token, chemins_relatives=['atomic-rejeu/fiche.pdf'], flux=[BytesIO(pdf)])
        t.terminer_session_televersement_lot(config, token)
        return t.creer_lot_session_televersement(config, token, creer_lot=lambda root: depot.creer_lot(index, root))
    ancien = creer()
    assert depot.executer_lot(index, ancien['id_lot'])['nb_echecs'] == 0
    nouveau = creer()
    monkeypatch.setattr(depot, '_SQL_LIGNE_MAJ', 'SELECT colonne_inexistante FROM table_inexistante WHERE %s = %s AND %s = %s AND %s = %s')
    with pytest.raises(psycopg2.errors.UndefinedTable):
        depot.executer_lot(index, nouveau['id_lot'], config=config, client_documents=StockageSimule())
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT object_key FROM documents WHERE path = %s', (str(ancien['racine'] / 'atomic-rejeu/fiche.pdf'),))
            assert curseur.fetchone()[0] is None
    assert depot.etat_lot(index, nouveau['id_lot'])['dossiers'][0]['statut'] == 'en_attente'


def test_fiche_validee_refuse_pdf_et_annexe_modifies_sans_mutation(base_depot: dict, tmp_path: Path) -> None:
    index = base_depot['index']
    source = (ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf').read_bytes()
    def dossier_session(jeton: str, pdf: bytes, annexe: bytes) -> Path:
        dossier = tmp_path / 'uploads' / 'lots-televerses' / jeton / 'fichiers' / 'rg11'
        dossier.mkdir(parents=True)
        (dossier / 'fiche.pdf').write_bytes(pdf)
        (dossier / 'annexe.txt').write_bytes(annexe)
        return dossier
    premier = dossier_session('ancien', source, b'original')
    assert depot.deposer_dossier(index, premier)['statut'] == 'traite'
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("UPDATE fiche SET statut='valide' WHERE code=%s", ('7792-SO',))
            curseur.execute('SELECT fichier_source, revision FROM fiche WHERE code=%s', ('7792-SO',))
            fiche_avant = curseur.fetchone()
            curseur.execute('SELECT p.id_document, p.chemin, p.empreinte_sha256 FROM fiche_piece_jointe p JOIN fiche f ON f.id_fiche=p.id_fiche WHERE f.code=%s ORDER BY p.id_piece', ('7792-SO',))
            pieces_avant = curseur.fetchall()
    nouveau = dossier_session('nouveau', source + b'\n% variante synthetique\n', b'modification')
    try:
        resultat = depot.deposer_dossier(index, nouveau)
        assert resultat['statut'] == 'echec'
        with index.connect() as connexion:
            with connexion.cursor() as curseur:
                curseur.execute('SELECT fichier_source, revision FROM fiche WHERE code=%s', ('7792-SO',))
                assert curseur.fetchone() == fiche_avant
                curseur.execute('SELECT p.id_document, p.chemin, p.empreinte_sha256 FROM fiche_piece_jointe p JOIN fiche f ON f.id_fiche=p.id_fiche WHERE f.code=%s ORDER BY p.id_piece', ('7792-SO',))
                assert curseur.fetchall() == pieces_avant
                curseur.execute('SELECT COUNT(*) FROM documents WHERE path LIKE %s', (str(nouveau) + '/%',))
                assert curseur.fetchone()[0] == 0
    finally:
        with index.connect() as connexion:
            with connexion.cursor() as curseur:
                curseur.execute("UPDATE fiche SET statut='a_valider' WHERE code=%s", ('7792-SO',))


def test_pdf_identique_annexe_modifiee_est_deposee_sans_ecraser_document(base_depot: dict, tmp_path: Path) -> None:
    index = base_depot['index']
    source = (ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf').read_bytes()
    def session(token: str, annexe: bytes) -> Path:
        dossier = tmp_path / 'uploads' / 'lots-televerses' / token / 'fichiers' / 'mise-a-jour'
        dossier.mkdir(parents=True)
        (dossier / 'fiche.pdf').write_bytes(source)
        (dossier / 'annexe.txt').write_bytes(annexe)
        return dossier
    premier = session('avant', b'version 1')
    assert depot.deposer_dossier(index, premier)['statut'] == 'traite'
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT id, content_hash FROM documents WHERE path=%s', (str(premier / 'annexe.txt'),))
            ancien_document, ancienne_empreinte = curseur.fetchone()
    second = session('apres', b'version 2')
    resultat = depot.deposer_dossier(index, second)
    assert resultat['statut'] == 'traite'
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT content_hash FROM documents WHERE id=%s', (ancien_document,))
            assert curseur.fetchone()[0] == ancienne_empreinte
            curseur.execute('SELECT id FROM documents WHERE path=%s', (str(second / 'annexe.txt'),))
            nouveau_document = curseur.fetchone()[0]
            assert nouveau_document != ancien_document
            curseur.execute('SELECT id_document FROM fiche_piece_jointe WHERE chemin=%s', (str(second / 'annexe.txt'),))
            assert curseur.fetchone()[0] == nouveau_document


def test_rejeu_avec_annexe_absente_exige_revue_sans_suppression(base_depot: dict, tmp_path: Path) -> None:
    index = base_depot['index']
    source = (ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf').read_bytes()
    def session(token: str, avec_annexe: bool) -> Path:
        dossier = tmp_path / 'uploads' / 'lots-televerses' / token / 'fichiers' / 'annexe-absente'
        dossier.mkdir(parents=True)
        (dossier / 'fiche.pdf').write_bytes(source)
        if avec_annexe:
            (dossier / 'annexe.txt').write_bytes(b'original a conserver')
        return dossier
    premier = session('avant', True)
    assert depot.deposer_dossier(index, premier)['statut'] == 'traite'
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT id_document FROM fiche_piece_jointe WHERE chemin=%s', (str(premier / 'annexe.txt'),))
            identifiant = curseur.fetchone()[0]
    second = session('apres', False)
    resultat = depot.deposer_dossier(index, second)
    assert resultat['statut'] == 'echec' and 'absentes' in resultat['raison']
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT id_document FROM fiche_piece_jointe WHERE chemin=%s', (str(premier / 'annexe.txt'),))
            assert curseur.fetchone()[0] == identifiant
    assert (premier / 'annexe.txt').read_bytes() == b'original a conserver'


def test_rejouer_pdf_historique_ne_declare_pas_la_fiche_courante_identique(base_depot: dict, tmp_path: Path) -> None:
    index = base_depot['index']
    source = (ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf').read_bytes()
    def session(token: str, pdf: bytes) -> Path:
        dossier = tmp_path / 'uploads' / 'lots-televerses' / token / 'fichiers' / 'historique'
        dossier.mkdir(parents=True)
        (dossier / 'fiche.pdf').write_bytes(pdf)
        return dossier
    avant = session('avant', source)
    assert depot.deposer_dossier(index, avant)['statut'] == 'traite'
    courant = session('courant', source + b'\n% nouvelle revision synthetique\n')
    assert depot.deposer_dossier(index, courant)['statut'] == 'traite'
    ancien_rejoue = session('rejeu', source)
    resultat = depot.deposer_dossier(index, ancien_rejoue)
    assert resultat['statut'] == 'echec'
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT cle_idempotence FROM lot_dossier WHERE id_fiche=(SELECT id_fiche FROM fiche WHERE code=%s) AND statut='traite' AND cle_idempotence IS NOT NULL ORDER BY traite_le DESC, id_lot_dossier DESC LIMIT 1", ('7792-SO',))
            assert curseur.fetchone()[0].rsplit('|', 1)[0] == str(courant / 'fiche.pdf')


def test_rejeu_meme_chemin_pdf_identique_annexe_modifiee(base_depot: dict, tmp_path: Path) -> None:
    index = base_depot['index']
    dossier = tmp_path / 'archive-identique'
    dossier.mkdir()
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', dossier / 'fiche.pdf')
    annexe = dossier / 'annexe.txt'
    annexe.write_bytes(b'v1')
    ancienne_empreinte = depot.empreinte_fichier(annexe)
    assert depot.deposer_dossier(index, dossier)['statut'] == 'traite'
    annexe.write_bytes(b'v2')
    resultat = depot.deposer_dossier(index, dossier)
    assert resultat['statut'] == 'echec'
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT content_hash FROM documents WHERE path=%s', (str(annexe),))
            assert curseur.fetchone()[0] == ancienne_empreinte


def test_pdf_modifie_meme_chemin_necrase_pas_le_document(base_depot: dict, tmp_path: Path) -> None:
    index = base_depot['index']
    dossier = tmp_path / 'archive-pdf-modifie'
    dossier.mkdir()
    pdf = dossier / 'fiche.pdf'
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', pdf)
    ancienne_empreinte = depot.empreinte_fichier(pdf)
    assert depot.deposer_dossier(index, dossier)['statut'] == 'traite'
    pdf.write_bytes(pdf.read_bytes() + b'\n% nouvelle revision synthetique\n')
    assert depot.deposer_dossier(index, dossier)['statut'] == 'echec'
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT content_hash FROM documents WHERE path=%s', (str(pdf),))
            assert curseur.fetchone()[0] == ancienne_empreinte


def test_modification_concurrente_apres_verification_rejeu_ne_signale_pas_deja_traite(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    index = base_depot['index']
    source = (ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf').read_bytes()
    def session(token: str, pdf: bytes) -> Path:
        dossier = tmp_path / 'uploads' / 'lots-televerses' / token / 'fichiers' / 'concurrence'
        dossier.mkdir(parents=True)
        (dossier / 'fiche.pdf').write_bytes(pdf)
        return dossier
    premier = session('ancien', source)
    assert depot.deposer_dossier(index, premier)['statut'] == 'traite'
    concurrent = session('concurrent', source + b'\n% modification concurrente synthetique\n')
    rejoue = session('rejeu', source)
    comparer = depot._comparer_pieces_rejeu
    def verifier_puis_modifier(*args, **kwargs):
        resultat = comparer(*args, **kwargs)
        assert depot.deposer_dossier(index, concurrent)['statut'] == 'traite'
        return resultat
    monkeypatch.setattr(depot, '_comparer_pieces_rejeu', verifier_puis_modifier)
    resultat = depot.deposer_dossier(index, rejoue)
    assert resultat['statut'] == 'echec'
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT cle_idempotence FROM lot_dossier WHERE id_fiche=(SELECT id_fiche FROM fiche WHERE code=%s) AND statut='traite' AND cle_idempotence IS NOT NULL ORDER BY traite_le DESC, id_lot_dossier DESC LIMIT 1", ('7792-SO',))
            assert curseur.fetchone()[0].rsplit('|', 1)[0] == str(concurrent / 'fiche.pdf')


def test_rejeu_verrouille_fiche_pendant_reverification(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg2

    index = base_depot['index']
    source = (ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf').read_bytes()
    def session(token: str) -> Path:
        dossier = tmp_path / 'uploads' / 'lots-televerses' / token / 'fichiers' / 'verrou-rejeu'
        dossier.mkdir(parents=True)
        (dossier / 'fiche.pdf').write_bytes(source)
        return dossier
    assert depot.deposer_dossier(index, session('ancien'))['statut'] == 'traite'
    verifier = depot._comparer_pieces_rejeu_curseur
    appels = []
    blocages = []
    def verifier_avec_concurrent(*args, **kwargs):
        appels.append(1)
        if len(appels) == 2:
            autre = psycopg2.connect(base_depot['url'])
            try:
                with autre.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '100ms'")
                    try:
                        cursor.execute('UPDATE fiche SET titre=%s WHERE code=%s', ('concurrent', '7792-SO'))
                    except psycopg2.errors.LockNotAvailable:
                        blocages.append(True)
                    else:
                        autre.commit()
            finally:
                autre.close()
        return verifier(*args, **kwargs)
    monkeypatch.setattr(depot, '_comparer_pieces_rejeu_curseur', verifier_avec_concurrent)
    assert depot.deposer_dossier(index, session('rejeu'))['statut'] == 'deja_traite'
    assert blocages == [True]


def test_annexe_modifiee_ne_restaure_pas_pdf_historique_apres_concurrence(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    index = base_depot['index']
    source = (ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf').read_bytes()
    def session(token: str, pdf: bytes, annexe: bytes) -> Path:
        dossier = tmp_path / 'uploads' / 'lots-televerses' / token / 'fichiers' / 'concurrence-annexe'
        dossier.mkdir(parents=True)
        (dossier / 'fiche.pdf').write_bytes(pdf)
        (dossier / 'annexe.txt').write_bytes(annexe)
        return dossier
    assert depot.deposer_dossier(index, session('ancien', source, b'v1'))['statut'] == 'traite'
    concurrent = session('concurrent', source + b'\n% nouveau pdf\n', b'v1')
    modification = session('modification', source, b'v2')
    comparer = depot._comparer_pieces_rejeu
    def comparer_puis_concurrent(*args, **kwargs):
        resultat = comparer(*args, **kwargs)
        assert depot.deposer_dossier(index, concurrent)['statut'] == 'traite'
        return resultat
    monkeypatch.setattr(depot, '_comparer_pieces_rejeu', comparer_puis_concurrent)
    resultat = depot.deposer_dossier(index, modification)
    assert resultat['statut'] == 'echec'
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT cle_idempotence FROM lot_dossier WHERE id_fiche=(SELECT id_fiche FROM fiche WHERE code=%s) AND statut='traite' AND cle_idempotence IS NOT NULL ORDER BY traite_le DESC, id_lot_dossier DESC LIMIT 1", ('7792-SO',))
            assert curseur.fetchone()[0].rsplit('|', 1)[0] == str(concurrent / 'fiche.pdf')


def test_annexe_modifiee_necrase_pas_annexe_concurrente(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    index = base_depot['index']
    source = (ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf').read_bytes()
    def session(token: str, annexe: bytes) -> Path:
        dossier = tmp_path / 'uploads' / 'lots-televerses' / token / 'fichiers' / 'annexe-concurrente'
        dossier.mkdir(parents=True)
        (dossier / 'fiche.pdf').write_bytes(source)
        (dossier / 'annexe.txt').write_bytes(annexe)
        return dossier
    assert depot.deposer_dossier(index, session('ancien', b'v1'))['statut'] == 'traite'
    concurrent = session('concurrent', b'v3')
    modification = session('modification', b'v2')
    comparer = depot._comparer_pieces_rejeu
    def comparer_puis_concurrent(*args, **kwargs):
        resultat = comparer(*args, **kwargs)
        monkeypatch.setattr(depot, '_comparer_pieces_rejeu', comparer)
        try:
            assert depot.deposer_dossier(index, concurrent)['statut'] == 'traite'
        finally:
            monkeypatch.setattr(depot, '_comparer_pieces_rejeu', comparer_puis_concurrent)
        return resultat
    monkeypatch.setattr(depot, '_comparer_pieces_rejeu', comparer_puis_concurrent)
    assert depot.deposer_dossier(index, modification)['statut'] == 'echec'
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT COUNT(*) FROM fiche_piece_jointe WHERE chemin=%s', (str(concurrent / 'annexe.txt'),))
            assert curseur.fetchone()[0] == 1
            curseur.execute('SELECT COUNT(*) FROM documents WHERE path=%s', (str(modification / 'annexe.txt'),))
            assert curseur.fetchone()[0] == 0


def test_s3_emule_http_lot_promeut_et_vues_lisent_sans_source(base_depot: dict, tmp_path: Path) -> None:
    """Test réseau S3rver opt-in : pas une qualification MinIO/R2."""
    import uuid
    from io import BytesIO

    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig
    from seamtech_search.fiches import televersement_lot as t
    from seamtech_search.jobs import create_job
    from seamtech_search.storage import S3StorageClient
    from seamtech_search.worker import _process_lot_task

    endpoint = os.environ.get('SEAMTECH_TEST_S3RVER_URL')
    if not endpoint:
        pytest.skip('S3rver local opt-in requis')
    racine_archive = tmp_path / 'archive'
    racine_archive.mkdir()
    config = AppConfig(root_paths=[racine_archive], database_path=tmp_path / 'data' / 'search.db',
                       database_url=base_depot['url'], min_free_bytes=0, storage_backend='s3',
                       s3_endpoint_url=endpoint, s3_bucket='seamtech-test-' + uuid.uuid4().hex[:12],
                       s3_access_key='S3RVER', s3_secret_key='S3RVER', auth_token='test-service-token')
    client_stockage = S3StorageClient(config=config)
    s3 = client_stockage._get_client()
    s3.create_bucket(Bucket=config.s3_bucket)
    fichiers = {'reseau/fiche.pdf': (ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf').read_bytes(),
                'reseau/annexe.txt': b'annexe synthetique test reseau'}
    try:
        token = t.creer_session_televersement_lot(config, nom_racine='TEST', nombre_fichiers=len(fichiers),
                                                   taille_totale_octets=sum(map(len, fichiers.values())))['token']
        t.recevoir_fichiers_televersement_lot(config, token, chemins_relatives=list(fichiers),
                                             flux=[BytesIO(data) for data in fichiers.values()])
        t.terminer_session_televersement_lot(config, token)
        index = base_depot['index']
        creation = t.creer_lot_session_televersement(config, token, creer_lot=lambda root: depot.creer_lot(index, root))
        job_id = f"lot-{creation['id_lot']}"
        create_job(index, job_id, str(creation['racine']), durability='process_memory')
        resultat = _process_lot_task({'job_id': job_id, 'id_lot': creation['id_lot']}, config, index, None,
                                     worker_id=None, heartbeat=None)
        assert resultat['status'] == 'completed'
        with index.connect() as connexion:
            with connexion.cursor() as curseur:
                curseur.execute('SELECT id, path, object_key, object_bucket FROM documents WHERE path = ANY(%s)',
                                ([str(creation['racine'] / n) for n in fichiers],))
                lignes = curseur.fetchall()
        assert len(lignes) == 2
        for _, path, key, bucket in lignes:
            assert bucket == config.s3_bucket and key
            assert s3.get_object(Bucket=bucket, Key=key)['Body'].read() == Path(path).read_bytes()
        sauvegarde_locale = tmp_path / 'sources-conservees'
        (creation['racine'] / 'reseau').rename(sauvegarde_locale)
        from seamtech_search.comptes.comptes import creer_utilisateur, ouvrir_session

        utilisateur = creer_utilisateur(index, 's3test-' + uuid.uuid4().hex[:8], 'Lecteur de test',
                                       'mot-de-passe-synthetique', role='approbateur', doit_changer_mot_de_passe=False)
        session = ouvrir_session(index, utilisateur['id_utilisateur'])
        client_http = TestClient(create_app(config))
        entetes = {'X-SEAMTECH-TOKEN': 'test-service-token', 'X-SEAMTECH-UTILISATEUR': utilisateur['identifiant'],
                   'X-SEAMTECH-ROLE': 'approbateur', 'X-SEAMTECH-SESSION': str(session['id_session']),
                   'X-SEAMTECH-SESSION-JETON': session['jeton']}
        for identifiant, path, _, _ in lignes:
            attendu = fichiers['reseau/' + Path(path).name]
            reponse = client_http.get(f'/pieces/{identifiant}/apercu', headers=entetes)
            assert reponse.status_code == 200, reponse.text
            assert reponse.content == attendu
            partielle = client_http.get(f'/pieces/{identifiant}/apercu', headers={**entetes, 'Range': 'bytes=0-4'})
            assert partielle.status_code == 206
            assert partielle.content == attendu[:5]
        assert all((sauvegarde_locale / Path(p).name).read_bytes() == fichiers['reseau/' + Path(p).name]
                   for _, p, _, _ in lignes)
    finally:
        liste = s3.list_objects_v2(Bucket=config.s3_bucket)
        for objet in liste.get('Contents', []):
            s3.delete_object(Bucket=config.s3_bucket, Key=objet['Key'])
        s3.delete_bucket(Bucket=config.s3_bucket)


def test_s3_emule_http_de_bout_en_bout_sans_source_locale(base_depot: dict, tmp_path: Path) -> None:
    """Transfert HTTP complet via S3rver : émulateur réseau, pas MinIO/R2."""
    import json
    import uuid
    from io import BytesIO

    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.comptes.comptes import creer_utilisateur, ouvrir_session
    from seamtech_search.config import AppConfig
    from seamtech_search.storage import S3StorageClient

    endpoint = os.environ.get('SEAMTECH_TEST_S3RVER_URL')
    if not endpoint:
        pytest.skip('S3rver local opt-in requis')
    archive = tmp_path / 'archive'
    archive.mkdir()
    config = AppConfig(root_paths=[archive], database_path=tmp_path / 'data' / 'search.db', database_url=base_depot['url'],
                       min_free_bytes=0, storage_backend='s3', s3_endpoint_url=endpoint,
                       s3_bucket='seamtech-test-' + uuid.uuid4().hex[:12], s3_access_key='S3RVER',
                       s3_secret_key='S3RVER', auth_token='test-service-token')
    s3 = S3StorageClient(config=config)._get_client()
    s3.create_bucket(Bucket=config.s3_bucket)
    index = base_depot['index']
    identifiant = 's3http-' + uuid.uuid4().hex[:8]
    utilisateur = creer_utilisateur(index, identifiant, 'Opérateur de test', 'mot-de-passe-synthetique',
                                    role='approbateur', doit_changer_mot_de_passe=False)
    session = ouvrir_session(index, utilisateur['id_utilisateur'])
    headers = {'X-SEAMTECH-TOKEN': 'test-service-token', 'X-SEAMTECH-UTILISATEUR': identifiant,
               'X-SEAMTECH-ROLE': 'approbateur', 'X-SEAMTECH-SESSION': str(session['id_session']),
               'X-SEAMTECH-SESSION-JETON': session['jeton']}
    fichiers = {'http/fiche.pdf': (ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf').read_bytes(),
                'http/test-http-annexe-unique.txt': b'annexe synthetique test http'}
    client = TestClient(create_app(config))
    try:
        reponse = client.post('/imports/dossier/televersements', headers=headers,
                              json={'nom_racine': 'TEST', 'nombre_fichiers': 2,
                                    'taille_totale_octets': sum(map(len, fichiers.values()))})
        assert reponse.status_code == 201, reponse.text
        token = reponse.json()['token']
        reponse = client.post(f'/imports/dossier/televersements/{token}/fichiers', headers=headers,
                              data={'chemins': json.dumps(list(fichiers))},
                              files=[('fichiers', (Path(nom).name, BytesIO(data), 'application/octet-stream'))
                                     for nom, data in fichiers.items()])
        assert reponse.status_code == 200, reponse.text
        reponse = client.post(f'/imports/dossier/televersements/{token}/terminer', headers=headers)
        assert reponse.status_code == 200, reponse.text
        reponse = client.post('/imports/dossier/lot', headers={**headers, 'X-SEAMTECH-BACKGROUND': 'false'},
                              json={'token_televersement': token})
        assert reponse.status_code == 202, reponse.text
        assert reponse.json()['etat']['nb_echecs'] == 0
        reponse = client.get('/fiches/7792-SO/pieces', headers=headers)
        assert reponse.status_code == 200, reponse.text
        pieces = {p['name']: p['id'] for p in reponse.json()['pieces']
                  if p.get('is_primary_pdf') or p['name'] == 'test-http-annexe-unique.txt'}
        assert len(pieces) == 2
        from seamtech_search.fiches.televersement_lot import racine_session_televersement_lot

        dossier_sources = racine_session_televersement_lot(config, token) / 'http'
        dossier_sources.rename(tmp_path / 'sources-conservees-http')
        # Simuler un scan complet qui ne voit plus ces sources : le catalogue
        # des copies distantes ne doit pas être supprimé avant leur lecture.
        with index.connect() as connexion:
            with connexion.cursor() as curseur:
                curseur.execute('SELECT id, path_key FROM documents')
                vus = {cle for identifiant_doc, cle in curseur.fetchall() if identifiant_doc not in pieces.values()}
        index.remove_missing(vus, scan_complete=True)
        for nom, identifiant_piece in pieces.items():
            reponse = client.get(f'/pieces/{identifiant_piece}/apercu', headers=headers)
            assert reponse.status_code == 200, reponse.text
            assert reponse.content == fichiers['http/' + nom]
    finally:
        liste = s3.list_objects_v2(Bucket=config.s3_bucket)
        for objet in liste.get('Contents', []):
            s3.delete_object(Bucket=config.s3_bucket, Key=objet['Key'])
        s3.delete_bucket(Bucket=config.s3_bucket)


def test_indexation_ne_modifie_pas_document_de_fiche_ni_empreinte_objet(base_depot: dict, tmp_path: Path) -> None:
    """Le hash du crawler (texte) ne doit jamais remplacer le hash brut d'un dépôt."""
    from seamtech_search.models import Document

    index = base_depot['index']
    dossier = tmp_path / 'scan-fiche'
    dossier.mkdir()
    pdf = dossier / 'fiche.pdf'
    annexe = dossier / 'annexe.txt'
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', pdf)
    annexe.write_bytes(b'original synthetique')
    objets = {str(p): {'object_key': 'objet-' + p.name, 'object_bucket': 'bucket-test',
                       'sha256': depot.empreinte_fichier(p)} for p in (pdf, annexe)}
    assert depot.deposer_dossier(index, dossier, objets_promus=objets)['statut'] == 'traite'
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT path, content_hash, object_key, object_bucket, upload_status, role FROM documents WHERE path IN (%s, %s) ORDER BY path', (str(pdf), str(annexe)))
            avant = curseur.fetchall()
    documents = [Document(path=p, name=p.name, parent_path=p.parent, extension=p.suffix,
                          size=p.stat().st_size, modified_at=p.stat().st_mtime,
                          is_dir=False, text='reindexation synthetique', content_hash='hash-de-texte')
                 for p in (pdf, annexe)]
    assert index.upsert_documents(documents) == 0
    with pytest.raises(RuntimeError, match='document.*fiche'):
        index.upsert_documents(documents, strict=True)
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT path, content_hash, object_key, object_bucket, upload_status, role FROM documents WHERE path IN (%s, %s) ORDER BY path', (str(pdf), str(annexe)))
            assert curseur.fetchall() == avant


def test_depot_accepte_document_precedemment_indexe_sans_objet(base_depot: dict, tmp_path: Path) -> None:
    """Un SHA de texte produit par le crawler n'est pas le SHA brut du dépôt."""
    from seamtech_search.models import Document

    index = base_depot['index']
    dossier = tmp_path / 'indexe-avant-depot'
    dossier.mkdir()
    pdf = dossier / 'fiche.pdf'
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', pdf)
    document = Document(path=pdf, name=pdf.name, parent_path=pdf.parent, extension='.pdf',
                        size=pdf.stat().st_size, modified_at=pdf.stat().st_mtime, is_dir=False,
                        content_hash='hash-texte-du-crawler')
    assert index.upsert_documents([document]) == 1
    resultat = depot.deposer_dossier(index, dossier)
    assert resultat['statut'] == 'traite', resultat
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT content_hash, role FROM documents WHERE path=%s', (str(pdf),))
            assert curseur.fetchone() == (depot.empreinte_fichier(pdf), 'fiche_pdf')


def test_depot_refuse_document_indexe_avec_objet_non_verifie(base_depot: dict, tmp_path: Path) -> None:
    """Le SHA texte du crawler ne prouve pas les octets déjà stockés."""
    from seamtech_search.models import Document

    index = base_depot['index']
    dossier = tmp_path / 'indexe-avec-objet'
    dossier.mkdir()
    pdf = dossier / 'fiche.pdf'
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', pdf)
    document = Document(path=pdf, name=pdf.name, parent_path=pdf.parent, extension='.pdf',
                        size=pdf.stat().st_size, modified_at=pdf.stat().st_mtime, is_dir=False,
                        content_hash='hash-texte', object_key='objet-preexistant',
                        object_bucket='bucket-test', upload_status='uploaded')
    assert index.upsert_documents([document]) == 1
    assert depot.deposer_dossier(index, dossier)['statut'] == 'echec'
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT content_hash, object_key, role FROM documents WHERE path=%s', (str(pdf),))
            assert curseur.fetchone() == ('hash-texte', 'objet-preexistant', None)


def test_indexation_stricte_annule_aussi_les_documents_non_proteges(base_depot: dict, tmp_path: Path) -> None:
    """Un import strict ne doit pas annoncer une publication partielle."""
    from seamtech_search.models import Document

    index = base_depot['index']
    dossier = tmp_path / 'index-strict'
    dossier.mkdir()
    pdf = dossier / 'fiche.pdf'
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', pdf)
    assert depot.deposer_dossier(index, dossier)['statut'] == 'traite'
    nouveau = dossier / 'nouveau.txt'
    documents = [Document(path=p, name=p.name, parent_path=p.parent, extension=p.suffix,
                          size=1, modified_at=1, is_dir=False) for p in (nouveau, pdf)]
    with pytest.raises(RuntimeError, match='document.*fiche'):
        index.upsert_documents(documents, strict=True)
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT COUNT(*) FROM documents WHERE path=%s', (str(nouveau),))
            assert curseur.fetchone()[0] == 0


def test_depot_verrouille_document_indexe_contre_approbation_concurrente(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """La création d'une autorisation ne doit pas traverser l'adoption du document."""
    import psycopg2

    from seamtech_search.comptes.comptes import creer_utilisateur
    from seamtech_search.models import Document

    index = base_depot['index']
    dossier = tmp_path / 'indexe-approbation'
    dossier.mkdir()
    pdf = dossier / 'fiche.pdf'
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', pdf)
    document = Document(path=pdf, name=pdf.name, parent_path=pdf.parent, extension='.pdf',
                        size=pdf.stat().st_size, modified_at=pdf.stat().st_mtime, is_dir=False,
                        content_hash='empreinte-texte')
    index.upsert_documents([document])
    utilisateur = creer_utilisateur(index, 'approbation-verrou', 'Utilisateur test', 'mot-de-passe-synthetique',
                                    role='approbateur', doit_changer_mot_de_passe=False)
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT id FROM documents WHERE path=%s', (str(pdf),))
            id_document = curseur.fetchone()[0]
    verrouiller = depot._verrouiller_document_existant
    bloque = []
    def verrouiller_puis_tester(curseur, chemin):
        verrouiller(curseur, chemin)
        if chemin == pdf:
            autre = psycopg2.connect(base_depot['url'])
            try:
                with autre.cursor() as c:
                    c.execute("SET LOCAL lock_timeout = '100ms'")
                    try:
                        c.execute("INSERT INTO approval_requests (id_demandeur, type_demande, id_piece, motif) VALUES (%s, 'telechargement', %s, 'test')",
                                  (utilisateur['id_utilisateur'], id_document))
                    except psycopg2.errors.LockNotAvailable:
                        bloque.append(True)
                    else:
                        autre.commit()
            finally:
                autre.close()
    monkeypatch.setattr(depot, '_verrouiller_document_existant', verrouiller_puis_tester)
    assert depot.deposer_dossier(index, dossier)['statut'] == 'traite'
    assert bloque == [True]


def test_depot_reserve_chemin_nouveau_contre_indexation_concurrente(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg2

    index = base_depot['index']
    dossier = tmp_path / 'nouveau-chemin-verrouille'
    dossier.mkdir()
    pdf = dossier / 'fiche.pdf'
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', pdf)
    verrouiller = depot._verrouiller_document_existant
    bloque = []
    def verrouiller_puis_tester(curseur, chemin):
        verrouiller(curseur, chemin)
        if chemin == pdf:
            autre = psycopg2.connect(base_depot['url'])
            try:
                with autre.cursor() as c:
                    c.execute("SET LOCAL lock_timeout = '100ms'")
                    try:
                        c.execute("INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, is_dir) VALUES (%s, %s, %s, %s, '.pdf', 1, 1, FALSE)",
                                  (str(pdf.resolve()), str(pdf), pdf.name, str(pdf.parent)))
                    except psycopg2.errors.LockNotAvailable:
                        bloque.append(True)
                    else:
                        autre.commit()
            finally:
                autre.close()
    monkeypatch.setattr(depot, '_verrouiller_document_existant', verrouiller_puis_tester)
    assert depot.deposer_dossier(index, dossier)['statut'] == 'traite'
    assert bloque == [True]


def test_indexation_preserve_document_objet_et_approbation_sans_role(base_depot: dict, tmp_path: Path) -> None:
    from seamtech_search.comptes.comptes import creer_utilisateur
    from seamtech_search.models import Document

    index = base_depot['index']
    utilisateur = creer_utilisateur(index, 'approbation-index', 'Utilisateur test', 'mot-de-passe-synthetique',
                                    role='approbateur', doit_changer_mot_de_passe=False)
    def document(p: Path, *, key: str | None = None) -> Document:
        return Document(path=p, name=p.name, parent_path=p.parent, extension='.txt', size=1, modified_at=1,
                        is_dir=False, content_hash='empreinte-originale', object_key=key,
                        object_bucket='bucket-test' if key else None, upload_status='uploaded' if key else 'pending')
    p1, p2 = tmp_path / 'objet.txt', tmp_path / 'approuve.txt'
    assert index.upsert_documents([document(p1, key='cle-originale'), document(p2)]) == 2
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT id FROM documents WHERE path=%s', (str(p2),))
            identifiant = curseur.fetchone()[0]
            curseur.execute("INSERT INTO approval_requests (id_demandeur, type_demande, id_piece, motif) VALUES (%s, 'telechargement', %s, 'test')",
                            (utilisateur['id_utilisateur'], identifiant))
    assert index.upsert_documents([document(p1), document(p2)]) == 0
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT path, content_hash, object_key FROM documents WHERE path IN (%s, %s) ORDER BY path', (str(p1), str(p2)))
            assert curseur.fetchall() == [(str(p2), 'empreinte-originale', None), (str(p1), 'empreinte-originale', 'cle-originale')]


def test_indexation_annule_si_approbation_concurrente_apres_upsert(base_depot: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg2
    import psycopg2.extras

    from seamtech_search.comptes.comptes import creer_utilisateur
    from seamtech_search.models import Document

    index = base_depot['index']
    chemin = tmp_path / 'course-approbation.txt'
    ancien = Document(path=chemin, name=chemin.name, parent_path=chemin.parent, extension='.txt',
                      size=1, modified_at=1, is_dir=False, content_hash='ancien')
    index.upsert_documents([ancien])
    with index.connect() as connexion:
        with connexion.cursor() as c:
            c.execute('SELECT id FROM documents WHERE path=%s', (str(chemin),))
            identifiant = c.fetchone()[0]
    utilisateur = creer_utilisateur(index, 'course-approbation-index', 'Utilisateur test', 'mot-de-passe-synthetique',
                                    role='approbateur', doit_changer_mot_de_passe=False)
    executer = psycopg2.extras.execute_values
    def apres_ecriture(*args, **kwargs):
        resultat = executer(*args, **kwargs)
        autre = psycopg2.connect(base_depot['url'])
        try:
            with autre.cursor() as c:
                c.execute("INSERT INTO approval_requests (id_demandeur, type_demande, id_piece, motif) VALUES (%s, 'telechargement', %s, 'test')",
                          (utilisateur['id_utilisateur'], identifiant))
            autre.commit()
        finally:
            autre.close()
        return resultat
    monkeypatch.setattr(psycopg2.extras, 'execute_values', apres_ecriture)
    nouveau = Document(path=chemin, name=chemin.name, parent_path=chemin.parent, extension='.txt',
                       size=2, modified_at=2, is_dir=False, content_hash='nouveau')
    with pytest.raises(RuntimeError, match='référence.*concurrente'):
        index.upsert_documents([nouveau])
    with index.connect() as connexion:
        with connexion.cursor() as c:
            c.execute('SELECT content_hash FROM documents WHERE id=%s', (identifiant,))
            assert c.fetchone()[0] == 'ancien'


def test_indexation_postgres_initialise_sans_migrations_metier(tmp_path: Path) -> None:
    """Le catalogue seul reste utilisable avant création des tables métier."""
    import uuid

    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    from seamtech_search.indexer import SearchIndex
    from seamtech_search.models import Document

    if not URL_PG:
        pytest.skip('PostgreSQL requis')
    nom = 'index_initial_' + uuid.uuid4().hex[:10]
    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with admin.cursor() as curseur:
        curseur.execute(f'CREATE DATABASE "{nom}"')
    idx = SearchIndex(tmp_path / 'unused.db', url_base_jetable(URL_PG, nom))
    try:
        idx.initialize()
        p = tmp_path / 'document.txt'
        doc = Document(path=p, name=p.name, parent_path=p.parent, extension='.txt', size=1, modified_at=1, is_dir=False)
        assert idx.upsert_documents([doc]) == 1
    finally:
        idx.close()
        with admin.cursor() as curseur:
            curseur.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()', (nom,))
            curseur.execute(f'DROP DATABASE "{nom}"')
        admin.close()


def test_scan_ne_supprime_pas_pdf_promu_quand_source_absente(base_depot: dict, tmp_path: Path) -> None:
    index = base_depot['index']
    dossier = tmp_path / 'source-manquante'
    dossier.mkdir()
    pdf = dossier / 'fiche.pdf'
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', pdf)
    objet = {str(pdf): {'object_key': 'copie-distante', 'object_bucket': 'bucket-test',
                        'sha256': depot.empreinte_fichier(pdf)}}
    assert depot.deposer_dossier(index, dossier, objets_promus=objet)['statut'] == 'traite'
    with index.connect() as connexion:
        with connexion.cursor() as c:
            c.execute('SELECT id FROM documents WHERE path=%s', (str(pdf),))
            identifiant = c.fetchone()[0]
    index.remove_missing(set(), scan_complete=True)
    with index.connect() as connexion:
        with connexion.cursor() as c:
            c.execute('SELECT object_key FROM documents WHERE id=%s', (identifiant,))
            assert c.fetchone() == ('copie-distante',)


def test_rebuild_initialise_conserve_catalogue_objet_sans_fk(tmp_path: Path) -> None:
    import uuid

    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    from seamtech_search.indexer import SearchIndex
    from seamtech_search.models import Document

    if not URL_PG:
        pytest.skip('PostgreSQL requis')
    nom = 'rebuild_objet_' + uuid.uuid4().hex[:10]
    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with admin.cursor() as c:
        c.execute(f'CREATE DATABASE "{nom}"')
    idx = SearchIndex(tmp_path / 'unused.db', url_base_jetable(URL_PG, nom))
    try:
        idx.initialize()
        p = tmp_path / 'objet.txt'
        doc = Document(path=p, name=p.name, parent_path=p.parent, extension='.txt', size=1, modified_at=1,
                       is_dir=False, object_key='objet-protege', object_bucket='bucket-test', upload_status='uploaded')
        idx.upsert_documents([doc])
        idx.initialize(rebuild=True)
        assert idx.rebuild_mode == 'en_place'
        with idx.connect() as connexion:
            with connexion.cursor() as c:
                c.execute('SELECT object_key FROM documents WHERE path=%s', (str(p),))
                assert c.fetchone() == ('objet-protege',)
    finally:
        idx.close()
        with admin.cursor() as c:
            c.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()', (nom,))
            c.execute(f'DROP DATABASE "{nom}"')
        admin.close()


def test_echec_scan_ne_supprime_pas_depot_concurrent(base_depot: dict, tmp_path: Path) -> None:
    """Le rollback du scan ne doit pas effacer un document créé après son snapshot."""
    index = base_depot['index']
    dossier = tmp_path / 'depot-concurrent-scan'
    dossier.mkdir()
    pdf = dossier / 'fiche.pdf'
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', pdf)
    objets = {str(pdf): {'object_key': 'copie-concurrente', 'object_bucket': 'bucket-test',
                         'sha256': depot.empreinte_fichier(pdf)}}
    from seamtech_search.indexer import SearchIndex

    concurrent = SearchIndex(tmp_path / 'concurrent.db', base_depot['url'])
    try:
        with pytest.raises(RuntimeError, match='panne scan'):
            with index.scan_snapshot():
                assert depot.deposer_dossier(concurrent, dossier, objets_promus=objets)['statut'] == 'traite'
                raise RuntimeError('panne scan')
    finally:
        concurrent.close()
    with index.connect() as connexion:
        with connexion.cursor() as c:
            c.execute('SELECT object_key FROM documents WHERE path=%s', (str(pdf),))
            assert c.fetchone() == ('copie-concurrente',)


def test_echec_scan_ne_retablit_pas_ancienne_version_document_adopte(base_depot: dict, tmp_path: Path) -> None:
    from seamtech_search.models import Document

    index = base_depot['index']
    dossier = tmp_path / 'document-adopte-pendant-scan'
    dossier.mkdir()
    pdf = dossier / 'fiche.pdf'
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', pdf)
    doc = Document(path=pdf, name=pdf.name, parent_path=pdf.parent, extension='.pdf',
                   size=pdf.stat().st_size, modified_at=pdf.stat().st_mtime, is_dir=False, content_hash='ancien-texte')
    index.upsert_documents([doc])
    objets = {str(pdf): {'object_key': 'copie-adoptee', 'object_bucket': 'bucket-test',
                         'sha256': depot.empreinte_fichier(pdf)}}
    from seamtech_search.indexer import SearchIndex

    concurrent = SearchIndex(tmp_path / 'concurrent.db', base_depot['url'])
    try:
        with pytest.raises(RuntimeError, match='panne scan'):
            with index.scan_snapshot():
                assert depot.deposer_dossier(concurrent, dossier, objets_promus=objets)['statut'] == 'traite'
                raise RuntimeError('panne scan')
    finally:
        concurrent.close()
    with index.connect() as connexion:
        with connexion.cursor() as c:
            c.execute('SELECT object_key, content_hash, role FROM documents WHERE path=%s', (str(pdf),))
            assert c.fetchone() == ('copie-adoptee', depot.empreinte_fichier(pdf), 'fiche_pdf')


def test_depot_refuse_document_indexe_portant_approbation(base_depot: dict, tmp_path: Path) -> None:
    from seamtech_search.comptes.comptes import creer_utilisateur
    from seamtech_search.models import Document

    index = base_depot['index']
    dossier = tmp_path / 'approbation-avant-depot'
    dossier.mkdir()
    pdf = dossier / 'fiche.pdf'
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', pdf)
    doc = Document(path=pdf, name=pdf.name, parent_path=pdf.parent, extension='.pdf', size=pdf.stat().st_size,
                   modified_at=pdf.stat().st_mtime, is_dir=False, content_hash='empreinte-texte')
    index.upsert_documents([doc])
    utilisateur = creer_utilisateur(index, 'avant-depot-approbation', 'Utilisateur test', 'mot-de-passe-synthetique',
                                    role='approbateur', doit_changer_mot_de_passe=False)
    with index.connect() as connexion:
        with connexion.cursor() as c:
            c.execute('SELECT id FROM documents WHERE path=%s', (str(pdf),))
            identifiant = c.fetchone()[0]
            c.execute("INSERT INTO approval_requests (id_demandeur, type_demande, id_piece, motif) VALUES (%s, 'telechargement', %s, 'test')",
                      (utilisateur['id_utilisateur'], identifiant))
    assert depot.deposer_dossier(index, dossier)['statut'] == 'echec'
    with index.connect() as connexion:
        with connexion.cursor() as c:
            c.execute('SELECT content_hash, role FROM documents WHERE id=%s', (identifiant,))
            assert c.fetchone() == ('empreinte-texte', None)
            c.execute('SELECT COUNT(*) FROM approval_requests WHERE id_piece=%s', (identifiant,))
            assert c.fetchone()[0] == 1


@pytest.mark.parametrize("erreur", [RuntimeError, depot.DepotImpossible])
def test_erreur_ecriture_depot_ne_divulgue_pas_exception_ou_chemin(base_depot, tmp_path, monkeypatch, caplog, erreur):
    secret = "SECRET-SYNTHETIQUE-TEST"
    dossier = tmp_path / secret
    dossier.mkdir()
    shutil.copy(ARCHIVE_7792 / "fiche-7792-SO_ffab.pdf", dossier / "fiche.pdf")
    def panne(*_args, **_kwargs):
        raise erreur(secret)
    monkeypatch.setattr(depot, "ecrire_fiche_resultat", panne)
    with caplog.at_level("INFO", logger=depot.LOGGER.name):
        resultat = depot.deposer_dossier(base_depot["index"], dossier)
    assert resultat["statut"] == "echec"
    assert erreur.__name__ in resultat["raison"]
    assert secret not in resultat["raison"]
    assert secret not in caplog.text
    assert all(record.exc_info is None for record in caplog.records if record.name == depot.LOGGER.name)
    with base_depot["index"].connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT raison FROM lot_dossier WHERE id_lot = %s", (resultat["id_lot"],))
            assert secret not in curseur.fetchone()[0]


def test_logs_depot_reussi_ne_divulguent_pas_dossier_ou_code(base_depot, tmp_path, caplog):
    secret = "SECRET-SYNTHETIQUE-TEST"
    dossier = tmp_path / secret
    dossier.mkdir()
    shutil.copy(ARCHIVE_7792 / "fiche-7792-SO_ffab.pdf", dossier / "fiche.pdf")
    code = depot.scanner_dossier(dossier, list(GABARITS_EMBARQUES)).fiche_extraite.code
    assert code
    with caplog.at_level("INFO", logger=depot.LOGGER.name):
        resultat = depot.deposer_dossier(base_depot["index"], dossier)
    assert resultat["statut"] in {"traite", "deja_traite"}
    assert secret not in caplog.text
    assert code not in caplog.text


def test_refus_scanner_ne_revele_pas_texte_exception_tierce(base_depot, tmp_path, monkeypatch, caplog):
    secret = "SECRET-SYNTHETIQUE-TEST"
    dossier = tmp_path / secret
    dossier.mkdir()
    def panne(*_args, **_kwargs):
        raise depot.DepotImpossible(secret)
    monkeypatch.setattr(depot, "scanner_dossier", panne)
    with caplog.at_level("INFO", logger=depot.LOGGER.name):
        resultat = depot.deposer_dossier(base_depot["index"], dossier)
    assert resultat["statut"] == "echec"
    assert secret not in resultat["raison"] and secret not in caplog.text
    with base_depot["index"].connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT raison FROM lot_dossier WHERE id_lot = %s", (resultat["id_lot"],))
            assert secret not in curseur.fetchone()[0]


def test_file_lot_erreur_inattendue_ne_divulgue_ni_chemin_ni_exception(base_depot, tmp_path, monkeypatch, caplog):
    secret = "SECRET-SYNTHETIQUE-TEST"
    racine = tmp_path / secret
    dossier = racine / "dossier"
    dossier.mkdir(parents=True)
    (dossier / "fiche.pdf").write_bytes(b"pdf synthetique")
    index = base_depot["index"]
    with caplog.at_level("INFO", logger=depot.LOGGER.name):
        id_lot = depot.creer_lot(index, racine)
        def panne(*_args, **_kwargs):
            raise RuntimeError(secret)
        monkeypatch.setattr(depot, "deposer_dossier", panne)
        etat = depot.executer_lot(index, id_lot)
    assert etat["nb_echecs"] == 1
    assert "RuntimeError" in etat["dossiers"][0]["raison"]
    assert secret not in etat["dossiers"][0]["raison"] and secret not in caplog.text
    assert all(record.exc_info is None for record in caplog.records if record.name == depot.LOGGER.name)


def test_meme_pdf_deja_passe_par_un_autre_flux_est_repris(base_depot: dict, tmp_path: Path) -> None:
    """MÊMES octets, ligne déjà écrite par un autre flux : le dépôt reprend la ligne.

    Régression verrouillée (CI « recette-corpus-reel », dépôt lot C REF-001..007) :
    le dépôt refusait (« Un PDF existant au même chemin a changé ») dès que le
    fichier était déjà passé par le flux d'import, parce que la comparaison ne
    portait que sur le texte indexé — l'import y met le texte extrait, le dépôt
    les métadonnées — alors que les OCTETS, eux, n'avaient pas changé. La ligne
    porte ici un objet et une identité d'octets, comme après un import.
    """
    index = base_depot['index']
    dossier = tmp_path / 'deja-importe'
    dossier.mkdir()
    pdf = dossier / 'fiche.pdf'
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', pdf)
    empreinte = depot.empreinte_fichier(pdf)

    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "INSERT INTO documents (path_key, path, name, parent_path, extension, size, "
                "modified_at, is_dir, content, content_hash, source_sha256, extraction_status, "
                "category, object_key, object_bucket) "
                "VALUES (%s, %s, %s, %s, '.pdf', %s, %s, FALSE, 'texte extrait sinthetique', "
                "'empreinte-texte', %s, 'extracted', 'analyzed', 'objets/1/source.pdf', 'seamtech-documents') "
                "RETURNING id",
                (
                    os.path.normcase(str(pdf.resolve())),
                    str(pdf),
                    pdf.name,
                    str(pdf.parent),
                    pdf.stat().st_size,
                    pdf.stat().st_mtime,
                    empreinte,
                ),
            )
            identifiant = curseur.fetchone()[0]

    resultat = depot.deposer_dossier(index, dossier)
    assert resultat['statut'] == 'traite', resultat

    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "SELECT source_sha256, object_key, role FROM documents WHERE id = %s", (identifiant,)
            )
            ligne = curseur.fetchone()
    # L'identité des octets est conservée (ce sont les mêmes) et la ligne porte
    # le rôle de PDF de fiche : la reprise est prouvée, pas un écrasement.
    assert ligne[0] == empreinte
    assert ligne[2] == 'fiche_pdf'
