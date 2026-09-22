"""Micro-correctifs de la revue du 21/09 — deux décisions figées par des tests.

1. RG12 / pièces jointes : un fichier est décrit UNE FOIS, dans ``documents``
   (métadonnées indexées plein-texte) ; ``fiche_piece_jointe`` est le LIEN
   (id_fiche ↔ id_document). Conséquence exigée : le fichier déposé est
   RETROUVABLE par les métadonnées (la recherche du lot E trouvera les croquis).
2. Deux dossiers, la même fiche : le second dépôt du même code fait l'objet
   d'un remplacement RG11 ; la fiche reste UNE et porte les pièces des DEUX
   dossiers ; chaque ligne lot_dossier pointe vers la même fiche.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

from seamtech_search.fiches import depot
from seamtech_search.fiches.gabarits import initialiser_gabarits
from seamtech_search.indexer import SearchIndex

RACINE = Path(__file__).resolve().parent.parent
ARCHIVE_7792 = RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"

URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.postgres  # couche métier PostgreSQL uniquement (§17.1)


@pytest.fixture(scope="module")
def base_pieces() -> Iterator[dict[str, Any]]:
    """Base jetable (migrations 001→011 comprises) + gabarits."""
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    nom_base = f"pieces_test_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{nom_base}"')
    finally:
        admin.close()
    url_base = URL_PG.rsplit("/", 1)[0] + f"/{nom_base}"
    index = SearchIndex(Path(f"/tmp/unused-pieces-{nom_base}.db"), url_base)
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
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                    (nom_base,),
                )
                cursor.execute(f'DROP DATABASE IF EXISTS "{nom_base}"')
        finally:
            admin.close()


class TestCatalogueUniquePieceJointe:
    def test_piece_decrite_une_fois_et_retrouvable(self, base_pieces: dict, tmp_path: Path) -> None:
        """Décision RG12 : la pièce déposée existe UNE fois dans `documents`
        (rôle posé, métadonnées indexées) et le lien porte id_document —
        la recherche plein-texte du lot E la trouvera par son nom."""
        index = base_pieces["index"]
        dossier = tmp_path / "AFFAIRE-PIECE"
        dossier.mkdir()
        shutil.copy(ARCHIVE_7792, dossier / "fiche.pdf")
        (dossier / "plan-atelier-croquis.pdf").write_bytes(b"%PDF-1.4 piece")
        resultat = depot.deposer_dossier(index, dossier)
        assert resultat["statut"] == "traite" and resultat["pieces"] == 1

        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                # une seule description : documents
                cursor.execute(
                    "SELECT id, role, content_hash, extraction_status FROM documents WHERE name = %s",
                    ("plan-atelier-croquis.pdf",),
                )
                lignes = cursor.fetchall()
                assert len(lignes) == 1, "la pièce est décrite plus d'une fois dans documents"
                id_document, role, empreinte, statut = lignes[0]
                assert role == "piece_jointe" and empreinte and statut == "metadata"
                # le lien porte la référence du catalogue
                cursor.execute(
                    "SELECT id_document FROM fiche_piece_jointe WHERE chemin = %s",
                    (str(dossier / "plan-atelier-croquis.pdf"),),
                )
                assert cursor.fetchone()[0] == id_document
                # RETROUVABLE par métadonnées (plein-texte, même configuration que l'indexeur ;
                # la recherche par FRAGMENT de nom — trigrammes — arrive avec le lot E)
                cursor.execute(
                    "SELECT COUNT(*) FROM documents WHERE search_vector @@ plainto_tsquery('seamtech_unaccent', 'plan-atelier-croquis.pdf')"
                )
                assert cursor.fetchone()[0] == 1, "la pièce jointe n'est pas retrouvable par son nom"
                cursor.execute(
                    "SELECT COUNT(*) FROM documents WHERE search_vector @@ plainto_tsquery('seamtech_unaccent', '7792-SO')"
                )
                assert cursor.fetchone()[0] >= 1, "la pièce n'est pas retrouvable par le code de sa fiche"

    def test_rejeu_ne_duplique_pas_la_description(self, base_pieces: dict, tmp_path: Path) -> None:
        """Rejouer le dossier : toujours UNE ligne documents (path_key stable)."""
        index = base_pieces["index"]
        dossier = tmp_path / "AFFAIRE-REJEU"
        dossier.mkdir()
        shutil.copy(ARCHIVE_7792, dossier / "fiche.pdf")
        (dossier / "croquis-voile.pdf").write_bytes(b"%PDF-1.4 croquis")
        depot.deposer_dossier(index, dossier)
        depot.deposer_dossier(index, dossier)  # rejeu
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM documents WHERE name = %s", ("croquis-voile.pdf",))
                assert cursor.fetchone()[0] == 1


class TestDeuxDossiersMemeFiche:
    def test_second_depot_meme_code_une_fiche_pieces_des_deux(self, base_pieces: dict, tmp_path: Path) -> None:
        """Comportement FIGÉ : deux dossiers portant le même PDF → une SEULE fiche
        (remplacement RG11), pièces des DEUX dossiers rattachées, et chaque
        lot_dossier pointe vers la même fiche. Le lien du premier dossier reste."""
        index = base_pieces["index"]
        dossier_a = tmp_path / "COMMANDE-A"
        dossier_b = tmp_path / "COMMANDE-B"
        dossier_a.mkdir()
        dossier_b.mkdir()
        shutil.copy(ARCHIVE_7792, dossier_a / "fiche.pdf")
        shutil.copy(ARCHIVE_7792, dossier_b / "fiche.pdf")  # même fiche, autre dossier
        (dossier_a / "croquis-A.pdf").write_bytes(b"%PDF-1.4 A")
        (dossier_b / "photo-B.pdf").write_bytes(b"%PDF-1.4 B")

        premier = depot.deposer_dossier(index, dossier_a)
        second = depot.deposer_dossier(index, dossier_b)
        assert premier["statut"] == "traite" and second["statut"] == "traite"

        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM fiche WHERE code = %s", ("7792-SO",))
                assert cursor.fetchone()[0] == 1, "le second dépôt a dupliqué la fiche"
                # les pièces des DEUX dossiers sont rattachées à la même fiche
                # (comptage par chemins exacts : la fiche peut porter d'autres
                # pièces déposées par d'autres tests sur la même base)
                cursor.execute(
                    "SELECT COUNT(*) FROM fiche_piece_jointe p JOIN fiche f ON f.id_fiche = p.id_fiche "
                    "WHERE f.code = %s AND p.chemin IN (%s, %s)",
                    ("7792-SO", str(dossier_a / "croquis-A.pdf"), str(dossier_b / "photo-B.pdf")),
                )
                assert cursor.fetchone()[0] == 2, "les pièces des deux dossiers doivent être rattachées"
                # chaque dépôt (chaque ligne lot_dossier) pointe vers la même fiche
                cursor.execute(
                    "SELECT DISTINCT id_fiche FROM lot_dossier WHERE chemin_dossier IN (%s, %s) AND id_fiche IS NOT NULL",
                    (str(dossier_a), str(dossier_b)),
                )
                cibles = cursor.fetchall()
                assert len(cibles) == 1, "les deux dépôts ne pointent pas la même fiche"
