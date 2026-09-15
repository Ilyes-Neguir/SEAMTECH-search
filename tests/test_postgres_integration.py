import os
import uuid
from pathlib import Path

import pytest

from seamtech_search.indexer import SearchIndex
from seamtech_search.models import Document


@pytest.mark.postgres
def test_postgres_initialize_upsert_search_and_health(tmp_path: Path) -> None:
    database_url = os.environ.get("SEAMTECH_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    index = SearchIndex(tmp_path / "unused.db", database_url)
    index.initialize()
    path = tmp_path / f"postgres-integration-{uuid.uuid4().hex}.txt"
    document = Document(
        path=path,
        name=path.name,
        parent_path=path.parent,
        extension=".txt",
        size=42,
        modified_at=1000.0,
        is_dir=False,
        text="PostgreSQL integration search marker",
    )

    try:
        assert index.upsert_document(document) is True
        results = index.search("PostgreSQL integration search marker")
        assert [result["name"] for result in results] == [path.name]

        stats = index.stats()
        assert stats.files >= 1
        assert index.health_details()["backend"] == "postgresql"
    finally:
        with index.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM documents WHERE path_key = %s", (document.path_key,))


@pytest.mark.postgres
def test_postgres_batch_upsert_searches_documents_and_preserves_categories(tmp_path: Path) -> None:
    database_url = os.environ.get("SEAMTECH_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    index = SearchIndex(tmp_path / "unused.db", database_url)
    index.initialize()
    analyzed_path = tmp_path / f"batch-analyzed-{uuid.uuid4().hex}.pdf"
    storage_path = tmp_path / f"batch-storage-{uuid.uuid4().hex}.txt"
    documents = [
        Document(
            path=analyzed_path,
            name=analyzed_path.name,
            parent_path=analyzed_path.parent,
            extension=".pdf",
            size=101,
            modified_at=1001.0,
            is_dir=False,
            text="analysismarker unique analyzed content",
            category="analyzed",
        ),
        Document(
            path=storage_path,
            name=storage_path.name,
            parent_path=storage_path.parent,
            extension=".txt",
            size=202,
            modified_at=1002.0,
            is_dir=False,
            text="storagemarker unique storage content",
            category="storage_direct",
        ),
    ]

    try:
        assert index.upsert_documents(documents) == 2

        analyzed_results = index.search("analysismarker")
        assert [result["name"] for result in analyzed_results] == [analyzed_path.name]
        assert analyzed_results[0]["category"] == "analyzed"

        storage_results = index.search("storagemarker")
        assert [result["name"] for result in storage_results] == [storage_path.name]
        assert storage_results[0]["category"] == "storage_direct"
    finally:
        with index.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM documents WHERE path_key = ANY(%s)",
                    ([document.path_key for document in documents],),
                )
