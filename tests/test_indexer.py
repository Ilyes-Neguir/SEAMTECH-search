from pathlib import Path

from seamtech_search.indexer import SearchIndex
from seamtech_search.models import Document


def test_search_finds_indexed_document(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "search.db")
    index.initialize(rebuild=True)
    file_path = tmp_path / "CLIENT-123-plan.pdf"

    document = Document(
        path=file_path,
        name=file_path.name,
        parent_path=file_path.parent,
        extension=".pdf",
        size=42,
        modified_at=1000.0,
        is_dir=False,
        text="technical drawing for customer reference ABC",
    )

    assert index.upsert_document(document) is True
    results = index.search("CLIENT-123")

    assert len(results) == 1
    assert results[0]["name"] == "CLIENT-123-plan.pdf"
    assert results[0]["match_type"] == "name"
    assert "<mark>CLIENT" in results[0]["snippet"]
    assert results[0]["extraction_status"] == "extracted"


def test_extraction_status_is_persisted(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "search.db")
    index.initialize(rebuild=True)
    file_path = tmp_path / "drawing.dwg"
    document = Document(
        path=file_path,
        name=file_path.name,
        parent_path=file_path.parent,
        extension=".dwg",
        size=6,
        modified_at=1.0,
        is_dir=False,
        extraction_status="unavailable",
        extraction_detail="unsupported file type",
    )

    assert index.upsert_document(document) is True
    result = index.search("drawing")[0]
    assert result["extraction_status"] == "unavailable"
    assert result["extraction_detail"] == "unsupported file type"


def test_unchanged_document_is_not_reindexed(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "search.db")
    index.initialize(rebuild=True)
    file_path = tmp_path / "same.docx"
    document = Document(
        path=file_path,
        name=file_path.name,
        parent_path=file_path.parent,
        extension=".docx",
        size=10,
        modified_at=10.0,
        is_dir=False,
        text="same content",
    )

    assert index.upsert_document(document) is True
    assert index.upsert_document(document) is False


def test_index_stats_counts_files_and_folders(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "search.db")
    index.initialize(rebuild=True)

    folder_path = tmp_path / "CLIENT-123"
    file_path = folder_path / "plan.pdf"
    folder = Document(
        path=folder_path,
        name=folder_path.name,
        parent_path=folder_path.parent,
        extension="",
        size=0,
        modified_at=1.0,
        is_dir=True,
    )
    file = Document(
        path=file_path,
        name=file_path.name,
        parent_path=file_path.parent,
        extension=".pdf",
        size=100,
        modified_at=2.0,
        is_dir=False,
        text="client drawing",
    )

    index.upsert_document(folder)
    index.upsert_document(file)
    stats = index.stats()

    assert stats.total_documents == 2
    assert stats.files == 1
    assert stats.folders == 1


def test_sqlite_preserve_objet_durant_indexation_suppression_et_rebuild(tmp_path: Path) -> None:
    import pytest

    index = SearchIndex(tmp_path / 'search.db')
    index.initialize()
    chemin = tmp_path / 'objet.pdf'
    initial = Document(path=chemin, name=chemin.name, parent_path=tmp_path, extension='.pdf',
                       size=3, modified_at=1, is_dir=False, object_key='cle-objet',
                       object_bucket='seau-test', upload_status='uploaded')
    assert index.upsert_documents([initial]) == 1
    nouveau = Document(path=chemin, name=chemin.name, parent_path=tmp_path, extension='.pdf',
                       size=4, modified_at=2, is_dir=False)
    assert index.upsert_documents([nouveau]) == 0
    with pytest.raises(RuntimeError, match='document.*objet'):
        index.upsert_documents([nouveau], strict=True)
    assert index.remove_missing(set(), scan_complete=True) == 0
    index.initialize(rebuild=True)
    assert index.rebuild_mode == 'en_place'
    with index.connect() as connexion:
        ligne = connexion.execute('SELECT object_key, object_bucket, size FROM documents WHERE path_key=?', (initial.path_key,)).fetchone()
        assert tuple(ligne) == ('cle-objet', 'seau-test', 3)


def test_sqlite_import_lie_objet_meme_si_metadonnees_inchangees(tmp_path: Path) -> None:
    from dataclasses import replace

    index = SearchIndex(tmp_path / 'search.db')
    index.initialize()
    chemin = tmp_path / 'source.pdf'
    initial = Document(path=chemin, name=chemin.name, parent_path=tmp_path, extension='.pdf',
                       size=3, modified_at=1, is_dir=False)
    assert index.upsert_documents([initial]) == 1
    avec_objet = replace(initial, object_key='cle-nouvelle', object_bucket='seau-test', upload_status='uploaded')
    assert index.upsert_documents([avec_objet], strict=True) == 1
    with index.connect() as connexion:
        ligne = connexion.execute('SELECT object_key, object_bucket, upload_status FROM documents WHERE path_key=?',
                                  (initial.path_key,)).fetchone()
        assert tuple(ligne) == ('cle-nouvelle', 'seau-test', 'uploaded')
