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