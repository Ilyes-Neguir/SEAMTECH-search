"""Correctifs du 30/10/2026 — client/bateau FFab et code de fiche manquant.

Défauts observés sur les archives réelles de l'atelier (jamais reproduits ici :
tous les PDF de ce module sont SYNTHÉTIQUES, seuls les libellés génériques et
l'organisation du document le sont) :

1. une fiche GV « FFab » exportée depuis un tableur met l'en-tête, la ligne
   d'atelier et la ligne de désignation dans des CELLULES séparées. La fenêtre
   de lecture s'arrêtait à quatre lignes physiques : le titre n'était jamais
   analysé, et la fiche sortait avec « Sans titre », client et bateau vides ;
2. la même ligne porte parfois une cellule voisine avant le titre
   (« … Fichier ZZ 075a dessiné par … ») qui devenait la désignation ;
3. la référence d'affaire entre parenthèses en fin de ligne
   (« (ZZ75 - XXXX) ») restait collée au nom du client ;
4. un PDF sans code lisible faisait échouer tout le dossier sur
   « écriture annulée : ValueError: Fiche sans code » — message sans issue,
   alors que la référence est souvent dans le NOM du fichier.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import psycopg2
import pytest
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from reportlab.pdfgen import canvas

from seamtech_search.fiches import depot
from seamtech_search.fiches.depot import code_depuis_nom_fichier, reprendre_code_depuis_nom
from seamtech_search.fiches.extraction import extraire_fiche
from seamtech_search.fiches.gabarits import GABARITS_EMBARQUES
from seamtech_search.fiches.modeles import FicheExtraite

LARGEUR, HAUTEUR = 595.0, 842.0  # A4 portrait, comme les fiches de l'atelier.
URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")
RACINE = Path(__file__).resolve().parent.parent
ARCHIVE_7792 = RACINE / "sample_data/CLIENT-7792-SO"


def _ecrire(chemin: Path, cellules: list[tuple[float, float, str]]) -> Path:
    pdf = canvas.Canvas(str(chemin), pagesize=(LARGEUR, HAUTEUR))
    pdf.setFont("Helvetica", 7)
    for x, haut, texte in cellules:
        pdf.drawString(x, HAUTEUR - haut, texte)
    pdf.save()
    return chemin


def _fiche_cellules(
    tmp_path: Path,
    *,
    nom: str = "ZZ 075a_ffab.pdf",
    titre: bool = True,
    code_document: str | None = "ZZ 075a",
) -> Path:
    """Mise en page en CELLULES : chaque cellule du tableau est une ligne."""
    cellules: list[tuple[float, float, str]] = [
        (150.0, 100.0, 'Fiche de fabrication "Grand-voile"'),
        (
            330.0,
            100.0,
            f"Fichier  {code_document} dessiné par A. le 18 septembre 2031"
            if code_document
            else "Fichier  feuille de calcul",
        ),
        # Trois cellules sur la MÊME rangée, à des hauteurs très légèrement
        # différentes : c'est cette rangée qui poussait le titre hors fenêtre.
        (20.0, 121.0, "Découpe et fabrication Atelier Démo"),
        (150.0, 121.7, "Tissu(s) (Pas de carbone): MATIERE-DEMO Blanc - White"),
        (430.0, 122.4, "Quantité: 2"),
    ]
    if titre:
        cellules.append(
            (
                20.0,
                143.0,
                "Grand-voile full-batten, gamme Demo, pour Bateau Demo 43 (43') "
                "de Client Demo (ZZ75 - XXXX)",
            )
        )
    cellules += [
        (20.0, 165.0, "Cotes (tissu):"),
        (330.0, 172.0, "Guindant   Bordure   Chute   Têtière   Surface   Poids (tissu)"),
        (330.0, 188.0, "12,00 m   4,00 m    12,50 m 15 cm     38,0 m²   20,0 kg"),
        (20.0, 210.0, "Montage fil V138 : 2 zigzag 6 temps"),
        (20.0, 230.0, "4 lattes"),
    ]
    return _ecrire(tmp_path / nom, cellules)


def _extraire(chemin: Path) -> FicheExtraite:
    return extraire_fiche(chemin, gabarits=list(GABARITS_EMBARQUES))


class TestTitreFfabEnCellules:
    def test_client_et_bateau_lus_malgre_une_rangee_de_cellules(self, tmp_path: Path) -> None:
        """Défaut n°1 : la fenêtre de titre doit franchir une rangée de cellules."""
        fiche = _extraire(_fiche_cellules(tmp_path))

        assert fiche.gabarit_code == "FICHE_GV_FFAB_V1"
        assert fiche.titre == "Grand-voile"
        assert fiche.client_nom == "Client Demo"
        assert fiche.bateau_nom == "Bateau Demo 43"
        assert fiche.bateau_taille == "43'"
        assert fiche.code == "ZZ 075A"

    def test_designation_non_polluee_par_une_cellule_voisine(self, tmp_path: Path) -> None:
        """Défaut n°2 : la désignation commence au mot de famille de voile."""
        fiche = _extraire(_fiche_cellules(tmp_path))
        trace = next(champ for champ in fiche.tous_les_champs() if champ.champ == "fiche.designation")

        assert trace.valeur_normalisee is not None
        assert trace.valeur_normalisee.startswith("Grand-voile")
        assert "Fichier" not in trace.valeur_normalisee
        assert "dessiné par" not in trace.valeur_normalisee

    def test_reference_entre_parentheses_devient_la_commande(self, tmp_path: Path) -> None:
        """Défaut n°3 : « (ZZ75 - XXXX) » n'est pas un morceau du nom du client."""
        fiche = _extraire(_fiche_cellules(tmp_path))

        assert fiche.commande_numero == "ZZ75 - XXXX"
        assert fiche.client_nom == "Client Demo"
        assert fiche.client_chantier is None

    def test_titre_absent_ne_devine_ni_client_ni_bateau(self, tmp_path: Path) -> None:
        """RG6 : sans ligne de titre, rien n'est inventé — la fiche reste à relire."""
        fiche = _extraire(_fiche_cellules(tmp_path, titre=False))

        assert fiche.client_nom is None
        assert fiche.bateau_nom is None
        assert fiche.code == "ZZ 075A"  # corroboré par la ligne « Fichier »


class TestCodeDepuisNomDeFichier:
    @pytest.mark.parametrize(
        ("nom", "attendu"),
        [
            ("ZZ 075a_ffab.pdf", "ZZ 075A"),
            ("ST 508_ffab.pdf", "ST 508"),
            ("fiche-7792-SO_ffab.pdf", "7792-SO"),
            ("fiche GV Full 250329 AJA.pdf", "250329 AJA"),
            ("250328 AJA.pdf", "250328 AJA"),
            ("genua.pdf", None),
            ("plan-voilier.pdf", None),
            ("2024.pdf", None),
        ],
    )
    def test_motif_conservateur(self, nom: str, attendu: str | None) -> None:
        assert code_depuis_nom_fichier(nom) == attendu

    def test_le_document_prime_sur_le_nom_de_fichier(self, tmp_path: Path) -> None:
        """Un code LU dans le PDF n'est jamais remplacé par celui du nom."""
        fiche = FicheExtraite(code="ZZ 999")

        assert reprendre_code_depuis_nom(fiche, tmp_path / "ZZ 075a_ffab.pdf") == "ZZ 999"
        assert fiche.code == "ZZ 999"

    def test_le_repli_trace_sa_provenance(self, tmp_path: Path) -> None:
        fiche = FicheExtraite()

        assert reprendre_code_depuis_nom(fiche, tmp_path / "ZZ 075a_ffab.pdf") == "ZZ 075A"
        trace = next(champ for champ in fiche.champs if champ.champ == "fiche.code")
        assert trace.methode == "nom_fichier"
        assert trace.confiance < 0.9, "un repli ne doit pas s'afficher comme une lecture certaine"
        assert trace.valeur_brute == "ZZ 075a_ffab.pdf"


# ---------------------------------------------------------------------------
# Dépôt réel (PostgreSQL) : plus jamais d'« écriture annulée : ValueError ».
# ---------------------------------------------------------------------------


@pytest.mark.postgres
class TestDepotSansCode:
    @pytest.fixture()
    def base(self, tmp_path: Path):
        if not URL_PG:
            pytest.skip("SEAMTECH_TEST_DATABASE_URL absent")
        nom_base = f"passe311{codepoint()}"
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

        index = SearchIndex(Path(f"/tmp/unused-311-{nom_base}.db"), url_base)
        index.initialize()
        index.run_migrations()
        initialiser_gabarits(index)
        try:
            yield index
        finally:
            index.close()
            admin = psycopg2.connect(URL_PG)
            admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            try:
                with admin.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()",
                        (nom_base,),
                    )
                    cursor.execute(f'DROP DATABASE IF EXISTS "{nom_base}"')
            finally:
                admin.close()

    def test_code_dans_le_nom_le_dossier_est_traite(self, base, tmp_path: Path) -> None:
        """Défaut n°4 (cas réel) : PDF sans code, nom « ZZ 075a_ffab.pdf » → fiche créée."""
        dossier = tmp_path / "AFFAIRE-SANS-CODE"
        dossier.mkdir()
        shutil.copy(_fiche_cellules(tmp_path, code_document=None), dossier / "ZZ 075a_ffab.pdf")

        resultat = depot.deposer_dossier(base, dossier)

        assert resultat["statut"] == "traite", resultat["raison"]
        assert resultat["fiche"] == "ZZ 075A"
        with base.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT f.code, f.titre, c.nom, b.nom, b.taille
                    FROM fiche f
                    LEFT JOIN client c ON c.id_client = f.id_client
                    LEFT JOIN bateau b ON b.id_bateau = f.id_bateau
                    WHERE f.code = %s
                    """,
                    ("ZZ 075A",),
                )
                code, titre, client, bateau, taille = cursor.fetchone()
        assert code == "ZZ 075A"
        assert titre == "Grand-voile"
        assert client == "Client Demo", "le client lu doit être persisté, plus « À vérifier »"
        assert bateau == "Bateau Demo 43"
        assert taille == "43'"

    def test_aucun_code_refus_explicite_sans_exception(self, base, tmp_path: Path) -> None:
        dossier = tmp_path / "AFFAIRE-MUETTE"
        dossier.mkdir()
        shutil.copy(_fiche_cellules(tmp_path, nom="genua.pdf", code_document=None), dossier / "genua.pdf")

        resultat = depot.deposer_dossier(base, dossier)

        assert resultat["statut"] == "echec"
        raison = resultat["raison"]
        assert "aucun code de fiche lisible" in raison
        assert "ValueError" not in raison, "le refus doit être lisible par un opérateur"
        assert "écriture annulée" not in raison
        # le refus est TRACÉ dans le lot, pas seulement retourné
        etat = depot.etat_lot(base, int(resultat["id_lot"]))
        assert etat["dossiers"][0]["raison"] == raison
        with base.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM fiche")
                assert int(cursor.fetchone()[0]) == 0, "un refus ne doit rien écrire"

    def test_fiche_7792_reste_identique(self, base, tmp_path: Path) -> None:
        """Non-régression : le corpus de référence garde son code lu dans le PDF."""
        dossier = tmp_path / "CLIENT-7792-SO"
        dossier.mkdir()
        shutil.copy(ARCHIVE_7792 / "fiche-7792-SO_ffab.pdf", dossier / "fiche-7792-SO_ffab.pdf")

        resultat = depot.deposer_dossier(base, dossier)

        assert resultat["statut"] == "traite"
        assert resultat["fiche"] == "7792-SO"


def codepoint() -> str:
    import uuid

    return uuid.uuid4().hex[:10]


# ---------------------------------------------------------------------------
# « Ça analyse mais ça n'ajoute rien dans la base » : passage EXPLICITE du
# rapport d'import à la fiche (POST /imports/{id}/creer-fiche).
# ---------------------------------------------------------------------------


@pytest.mark.postgres
class TestCreerFicheDepuisImport:
    @pytest.fixture()
    def contexte(self, tmp_path: Path):
        import uuid

        from fastapi.testclient import TestClient
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

        if not URL_PG:
            pytest.skip("SEAMTECH_TEST_DATABASE_URL absent")
        nom_base = f"import_fiche_{uuid.uuid4().hex[:10]}"
        admin = psycopg2.connect(URL_PG)
        admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{nom_base}"')
        finally:
            admin.close()
        url_base = URL_PG.rsplit("/", 1)[0] + f"/{nom_base}"
        from seamtech_search.api import create_app
        from seamtech_search.config import AppConfig
        from seamtech_search.fiches.gabarits import initialiser_gabarits
        from seamtech_search.indexer import SearchIndex

        index = SearchIndex(Path(f"/tmp/unused-if-{nom_base}.db"), url_base)
        index.initialize()
        index.run_migrations()
        initialiser_gabarits(index)
        config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "unused.db", database_url=url_base)
        try:
            yield {"index": index, "client": TestClient(create_app(config)), "racine": tmp_path}
        finally:
            index.close()
            admin = psycopg2.connect(URL_PG)
            admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            try:
                with admin.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()",
                        (nom_base,),
                    )
                    cursor.execute(f'DROP DATABASE IF EXISTS "{nom_base}"')
            finally:
                admin.close()

    @staticmethod
    def _inserer_import(index, id_import: str, pdf: str | None) -> None:
        import json

        payload = {"import_id": id_import, "technical_pdf": pdf, "status": "needs_review"}
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO imports (id, source_path, status, payload, created_at) "
                    "VALUES (%s, %s, %s, %s, now())",
                    (id_import, "/depot", "needs_review", json.dumps(payload)),
                )

    def test_import_inconnu_404(self, contexte) -> None:
        reponse = contexte["client"].post("/imports/inconnu/creer-fiche")
        assert reponse.status_code == 404

    def test_import_sans_pdf_technique_refuse_en_expliquant(self, contexte) -> None:
        self._inserer_import(contexte["index"], "imp-sans-pdf", None)

        reponse = contexte["client"].post("/imports/imp-sans-pdf/creer-fiche")

        assert reponse.status_code == 422
        assert "aucun PDF technique" in reponse.json()["detail"]
        with contexte["index"].connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM fiche")
                assert cursor.fetchone()[0] == 0

    def test_import_avec_pdf_cree_la_fiche(self, contexte) -> None:
        dossier = contexte["racine"] / "AFFAIRE-IMPORT"
        dossier.mkdir()
        pdf = shutil.copy(_fiche_cellules(contexte["racine"]), dossier / "ZZ 075a_ffab.pdf")
        self._inserer_import(contexte["index"], "imp-avec-pdf", str(pdf))

        reponse = contexte["client"].post("/imports/imp-avec-pdf/creer-fiche")

        assert reponse.status_code == 201, reponse.text
        corps = reponse.json()
        assert corps["statut"] == "traite"
        assert corps["fiche"] == "ZZ 075A"
        assert corps["dossier"] == str(dossier)
        with contexte["index"].connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("SELECT code FROM fiche")
                assert [ligne[0] for ligne in cursor.fetchall()] == ["ZZ 075A"]

    def test_dossier_hors_racines_refuse(self, contexte, tmp_path_factory) -> None:
        ailleurs = tmp_path_factory.mktemp("hors-racines") / "AFFAIRE-EXTERNE"
        ailleurs.mkdir()
        shutil.copy(_fiche_cellules(contexte["racine"]), ailleurs / "ZZ 075a_ffab.pdf")
        self._inserer_import(contexte["index"], "imp-hors-racines", str(ailleurs / "ZZ 075a_ffab.pdf"))

        reponse = contexte["client"].post("/imports/imp-hors-racines/creer-fiche")

        assert reponse.status_code == 403
        with contexte["index"].connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM fiche")
                assert cursor.fetchone()[0] == 0


class TestRefusExplique:
    def test_pdf_sans_libelle_de_fiche_explique_au_lieu_de_gabarit_inconnu(self, tmp_path: Path) -> None:
        """« gabarit inconnu » seul ressemble à une panne ; la cause est documentaire."""
        dossier = tmp_path / "PLAN-SEUL"
        dossier.mkdir()
        _ecrire(dossier / "plan.pdf", [(20.0, 60.0, "Plan de voilure cotes generales 12,40 m")])

        plan = depot.scanner_dossier(dossier, list(GABARITS_EMBARQUES))

        assert not plan.accepte
        assert "aucune fiche de fabrication reconnue" in plan.raison_refus
        assert "plan.pdf" in plan.raison_refus, "l'opérateur doit savoir quel fichier a été examiné"
        assert "n'ont pas été modifiés" in plan.raison_refus
