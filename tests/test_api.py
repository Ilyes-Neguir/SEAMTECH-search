from pathlib import Path

from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.models import Document


def test_configured_token_protects_search(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config = AppConfig(root_paths=[root], database_path=tmp_path / "search.db", auth_token="secret")
    client = TestClient(create_app(config))

    assert client.get("/search?q=client").status_code == 401
    assert client.get("/search?q=client", headers={"X-SEAMTECH-TOKEN": "secret"}).status_code == 200
    assert client.get("/health").status_code == 200


def test_search_pagination_contract(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    database = tmp_path / "search.db"
    index = SearchIndex(database)
    index.initialize(rebuild=True)
    for number in range(3):
        path = root / f"reference-{number}.txt"
        index.upsert_document(Document(path, path.name, root, ".txt", 10, float(number), False, "factory reference"))
    config = AppConfig(root_paths=[root], database_path=database)
    client = TestClient(create_app(config))
    response = client.get("/search?q=reference&limit=2&offset=1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["offset"] == 1
    assert payload["limit"] == 2
    assert payload["count"] == 2
    assert payload["has_more"] is False


def test_local_mode_allows_search_without_token(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config = AppConfig(root_paths=[root], database_path=tmp_path / "search.db")
    client = TestClient(create_app(config))
    assert client.get("/search?q=client").status_code == 200


def test_search_exposes_extraction_status(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    database = tmp_path / "search.db"
    index = SearchIndex(database)
    index.initialize(rebuild=True)
    path = root / "drawing.dwg"
    index.upsert_document(
        Document(
            path, path.name, root, ".dwg", 10, 1.0, False,
            extraction_status="unavailable", extraction_detail="unsupported file type",
        )
    )
    client = TestClient(create_app(AppConfig(root_paths=[root], database_path=database)))

    result = client.get("/search?q=drawing").json()["results"][0]

    assert result["extraction_status"] == "unavailable"
    assert result["extraction_detail"] == "unsupported file type"
