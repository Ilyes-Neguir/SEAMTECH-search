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
    marker = uuid.uuid4().hex
    path = tmp_path / f"postgres-integration-{marker}.txt"
    document = Document(
        path=path,
        name=path.name,
        parent_path=path.parent,
        extension=".txt",
        size=42,
        modified_at=1000.0,
        is_dir=False,
        text=f"PostgreSQL integration search marker {marker}",
    )

    try:
        assert index.upsert_document(document) is True
        # Search a token unique to THIS run, not a fixed phrase.
        # _search_postgres matches with `search_vector @@ query_or`, so a
        # multi-word query ORs its terms and matches any row in the database
        # containing any of them. These tests share one PostgreSQL server for
        # the whole CI job, and since the dedicated `-m postgres` step now runs
        # before the coverage step they execute twice against it -- so a fixed
        # phrase like "PostgreSQL integration search marker" also matched the
        # row the earlier step had inserted under a different uuid, and the
        # exact-equality assertion below failed on a healthy index. Uniqueness
        # has to live in the searched text, not just in the filename.
        results = index.search(marker, limit=200)
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
    marker = uuid.uuid4().hex
    analyzed_path = tmp_path / f"batch-analyzed-{marker}.pdf"
    storage_path = tmp_path / f"batch-storage-{marker}.txt"
    documents = [
        Document(
            path=analyzed_path,
            name=analyzed_path.name,
            parent_path=analyzed_path.parent,
            extension=".pdf",
            size=101,
            modified_at=1001.0,
            is_dir=False,
            text=f"analysismarker{marker} unique analyzed content",
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
            text=f"storagemarker{marker} unique storage content",
            category="storage_direct",
        ),
    ]

    try:
        assert index.upsert_documents(documents) == 2

        # Unique per run for the same reason as the test above: these are single
        # tokens, but they were still fixed strings, so the second execution in
        # the same job matched the rows the first one had inserted.
        analyzed_results = index.search(f"analysismarker{marker}", limit=200)
        assert [result["name"] for result in analyzed_results] == [analyzed_path.name]
        assert analyzed_results[0]["category"] == "analyzed"

        storage_results = index.search(f"storagemarker{marker}", limit=200)
        assert [result["name"] for result in storage_results] == [storage_path.name]
        assert storage_results[0]["category"] == "storage_direct"
    finally:
        with index.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM documents WHERE path_key = ANY(%s)",
                    ([document.path_key for document in documents],),
                )


@pytest.mark.postgres
def test_postgres_upsert_document_with_uploaded_at_roundtrips(tmp_path: Path) -> None:
    database_url = os.environ.get("SEAMTECH_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    index = SearchIndex(tmp_path / "unused.db", database_url)
    index.initialize()
    index.run_migrations()
    path = tmp_path / f"postgres-uploaded-at-{uuid.uuid4().hex}.txt"
    uploaded_at = 1_700_000_000.25
    document = Document(
        path=path,
        name=path.name,
        parent_path=path.parent,
        extension=".txt",
        size=7,
        modified_at=1000.0,
        is_dir=False,
        text="uploaded at roundtrip marker",
        object_key="imports/test/uploaded-at.txt",
        object_bucket="seamtech-documents",
        uploaded_at=uploaded_at,
        upload_status="uploaded",
    )

    try:
        assert index.upsert_document(document) is True
        with index.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT uploaded_at, pg_typeof(uploaded_at)::text FROM documents WHERE path_key = %s",
                    (document.path_key,),
                )
                stored, column_type = cursor.fetchone()

        assert column_type == "double precision"
        assert stored == pytest.approx(uploaded_at)
    finally:
        with index.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM documents WHERE path_key = %s", (document.path_key,))
