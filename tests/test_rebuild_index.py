"""Rebuild d'index SÛR — SQLite (défaut E-40).

Le même ``execute`` que sur PostgreSQL : ``DROP TABLE documents`` « réussissait »
en laissant ``id_document`` pointer dans le vide, parce que SQLite n'applique pas
les clés étrangères par défaut. La politique corrigée est la même des deux
côtés : dès qu'une table référence réellement un document, la table est
CONSERVÉE avec ses identifiants (rebuild « en place ») ; sinon seulement, le
comportement historique de recréation est conservé.

Le volet PostgreSQL des cinq preuves de la revue vit dans
``tests/test_rebuild_index_postgres.py``.
"""

from __future__ import annotations

from pathlib import Path

from tests.reconstruire_aide import (
    ecrire_config,
    id_document,
    identifiants_documents,
    indexer,
    peupler_dossiers,
)

# Table de liens, copie de la jointure métier ``fiche_piece_jointe`` : SQLite
# n'a pas les tables métier (elles vivent dans PostgreSQL), donc le test crée
# lui-même la table référençante pour éprouver le garde-fou.
DDL_LIEN = (
    "CREATE TABLE fiche_piece_jointe ("
    " id_piece INTEGER PRIMARY KEY AUTOINCREMENT,"
    " id_fiche INTEGER NOT NULL,"
    " chemin TEXT NOT NULL,"
    " id_document INTEGER REFERENCES documents(id))"
)


def test_rebuild_sqlite_conserve_les_documents_references(tmp_path: Path) -> None:
    from seamtech_search.config import AppConfig
    from seamtech_search.indexer import SearchIndex

    racine = tmp_path / "documents"
    peupler_dossiers(racine)
    config = AppConfig.load(str(ecrire_config(tmp_path, racine)))
    index = SearchIndex(tmp_path / "search.db")
    index.initialize()
    try:
        indexer(index, config)
        avant = identifiants_documents(index)
        id_reference = id_document(index, "dossier-client/fiche-A.txt")
        with index.connect() as connexion:
            connexion.execute(DDL_LIEN)
            connexion.execute(
                "INSERT INTO fiche_piece_jointe (id_fiche, chemin, id_document) VALUES (?, ?, ?)",
                (1, "dossier-client/fiche-A.txt", id_reference),
            )

        index.initialize(rebuild=True)

        # EN PLACE : les identifiants sont conservés, donc pas de lien orphelin.
        assert index.rebuild_mode == "en_place", index.rebuild_mode
        assert identifiants_documents(index) == avant
        with index.connect() as connexion:
            lignes = connexion.execute(
                "SELECT d.path_key FROM fiche_piece_jointe p JOIN documents d ON d.id = p.id_document"
            ).fetchall()
        assert len(lignes) == 1 and str(lignes[0][0]).endswith("dossier-client/fiche-A.txt"), lignes
    finally:
        index.close()


def test_rebuild_sqlite_recreation_quand_aucun_lien(tmp_path: Path) -> None:
    """Sans référence vers un document, le comportement historique est conservé."""
    from seamtech_search.config import AppConfig
    from seamtech_search.indexer import SearchIndex

    racine = tmp_path / "documents"
    peupler_dossiers(racine)
    config = AppConfig.load(str(ecrire_config(tmp_path, racine)))
    index = SearchIndex(tmp_path / "search.db")
    index.initialize()
    try:
        indexer(index, config)
        assert identifiants_documents(index)  # l'index initial n'était pas vide
        with index.connect() as connexion:
            connexion.execute(DDL_LIEN)  # table présente, mais AUCUNE ligne

        index.initialize(rebuild=True)

        assert index.rebuild_mode == "recreation", index.rebuild_mode
        assert identifiants_documents(index) == {}
        # Le scan suivant réindexe bien tout le corpus : rien n'est perdu.
        rapport = indexer(index, config)
        assert rapport["scanned"] >= 2
        assert len(identifiants_documents(index)) >= 2
    finally:
        index.close()
