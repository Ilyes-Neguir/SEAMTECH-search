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
