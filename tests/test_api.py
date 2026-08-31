from pathlib import Path

from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig


def test_configured_token_protects_search(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config = AppConfig(root_paths=[root], database_path=tmp_path / "search.db", auth_token="secret")
    client = TestClient(create_app(config))

    assert client.get("/search?q=client").status_code == 401
    assert client.get("/search?q=client", headers={"X-SEAMTECH-TOKEN": "secret"}).status_code == 200
    assert client.get("/health").status_code == 200


def test_local_mode_allows_search_without_token(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config = AppConfig(root_paths=[root], database_path=tmp_path / "search.db")
    client = TestClient(create_app(config))
    assert client.get("/search?q=client").status_code == 200
