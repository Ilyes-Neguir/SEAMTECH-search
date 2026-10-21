"""Lot 0b — /docs /redoc /openapi.json fermés par défaut (SEAMTECH_EXPOSE_DOCS=false).

Vérifie les trois états:
- par défaut (expose_docs=False) => 404 sur /docs, /redoc, /openapi.json
- SEAMTECH_EXPOSE_DOCS=true => 200
- SEAMTECH_EXPOSE_DOCS=false explicite => 404
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex


def _app(tmp_path: Path, expose: bool | None):
    # tmp_path is used as dummy index path; pas besoin de PG pour ce test
    index = SearchIndex(tmp_path / "docs_test.db")
    index.initialize()
    extra = {}
    if expose is not None:
        extra["expose_docs"] = expose
    config = AppConfig(root_paths=[tmp_path], database_url="", min_free_bytes=0, auth_token="secret-test", **extra)
    # Force expose via env si True/False donné explicitement
    if expose is True:
        config.expose_docs = True
    elif expose is False:
        config.expose_docs = False
    app = create_app(config)
    return app


def test_docs_fermes_par_defaut(tmp_path: Path):
    app = _app(tmp_path, None)
    client = TestClient(app)
    for path in ["/docs", "/redoc", "/openapi.json"]:
        resp = client.get(path)
        assert resp.status_code == 404, f"{path} doit être 404 par défaut, got {resp.status_code}"

    # Health tetap ouvert
    assert client.get("/health").status_code == 200


def test_docs_ouverts_si_expose_true(tmp_path: Path):
    app = _app(tmp_path, True)
    client = TestClient(app)
    # docs doit être accessible (200) quand expose true
    resp = client.get("/docs")
    assert resp.status_code == 200, f"/docs doit être 200 quand expose_docs=true, got {resp.status_code}"
    resp2 = client.get("/openapi.json")
    assert resp2.status_code == 200


def test_docs_fermes_si_expose_false_explicite(tmp_path: Path):
    app = _app(tmp_path, False)
    client = TestClient(app)
    for path in ["/docs", "/redoc", "/openapi.json"]:
        resp = client.get(path)
        assert resp.status_code == 404, f"{path} doit être 404 quand expose_docs=false"


def test_docs_via_env(tmp_path: Path):
    orig = os.environ.get("SEAMTECH_EXPOSE_DOCS")
    try:
        os.environ["SEAMTECH_EXPOSE_DOCS"] = "true"
        index = SearchIndex(tmp_path / "env_true.db")
        index.initialize()
        config = AppConfig(root_paths=[tmp_path], database_url="", min_free_bytes=0, auth_token="s")
        assert config.expose_docs is True
        app = create_app(config)
        assert TestClient(app).get("/docs").status_code == 200

        os.environ["SEAMTECH_EXPOSE_DOCS"] = "false"
        index2 = SearchIndex(tmp_path / "env_false.db")
        index2.initialize()
        config2 = AppConfig(root_paths=[tmp_path], database_url="", min_free_bytes=0, auth_token="s")
        assert config2.expose_docs is False
        app2 = create_app(config2)
        assert TestClient(app2).get("/docs").status_code == 404
    finally:
        if orig is None:
            os.environ.pop("SEAMTECH_EXPOSE_DOCS", None)
        else:
            os.environ["SEAMTECH_EXPOSE_DOCS"] = orig
