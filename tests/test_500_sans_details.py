"""Lot 0b — 500 génériques sans détails internes + X-Request-ID.

Vérifie que les handlers 500 ne fuient pas stack/SQL/path et que le log serveur contient request_id.
"""
from __future__ import annotations

import pathlib
import re

import pytest
from fastapi import HTTPException


def test_brouillon_500_generique():
    """fiches/routes.py:2098 doit répondre 'Erreur interne lors de la génération du brouillon.'"""
    p = pathlib.Path("seamtech_search/fiches/routes.py").read_text(encoding="utf-8")
    assert 'raise HTTPException(status_code=500, detail="Erreur interne lors de la génération du brouillon.")' in p
    assert "f\"Échec génération brouillon: {exc}\"" not in p


def test_reindex_500_generique():
    p = pathlib.Path("seamtech_search/bateaux/routes.py").read_text(encoding="utf-8")
    assert 'raise HTTPException(status_code=500, detail="Erreur interne lors de la réindexation.")' in p
    assert "Réindexation échouée : {exc}" not in p


def test_comparatif_500_generique():
    p = pathlib.Path("seamtech_search/bateaux/routes.py").read_text(encoding="utf-8")
    assert 'raise HTTPException(status_code=500, detail="Erreur interne lors de la génération du comparatif.")' in p
    assert "Comparatif échoué : {exc}" not in p


def test_generic_handler_present():
    p = pathlib.Path("seamtech_search/api.py").read_text(encoding="utf-8")
    assert "def generic_exception_handler" in p
    assert '"Erreur interne."' in p
    assert "X-Request-ID" in p
    assert "request.state.request_id" in p


def test_pas_de_fuite_stack_dans_detail():
    """Aucun detail 500 ne doit contenir stack/traceback/pg_/SELECT/path."""
    for rel in ["seamtech_search/fiches/routes.py", "seamtech_search/bateaux/routes.py", "seamtech_search/api.py"]:
        txt = pathlib.Path(rel).read_text(encoding="utf-8")
        # Cherche les lignes raise HTTPException(status_code=500
        for i, ligne in enumerate(txt.splitlines(), 1):
            if "status_code=500" in ligne:
                # Le detail ne doit pas interpoler l'exception
                assert "{exc}" not in ligne, f"{rel}:{i} fuite exc dans 500: {ligne}"
                assert "traceback" not in ligne.lower()
                assert "stack" not in ligne.lower()

def test_api_generic_handler_ne_fuit_pas_exception_text(tmp_path):
    """Le handler global 500 renvoie toujours 'Erreur interne.' même si l'exception contient des secrets."""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI, Request
    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig
    from seamtech_search.indexer import SearchIndex

    index = SearchIndex(tmp_path / "500test.db")
    index.initialize()
    config = AppConfig(root_paths=[tmp_path], database_url="", min_free_bytes=0)
    app = create_app(config)

    # Ajoute une route qui lève une exception avec des secrets
    @app.get("/test-500-leak")
    def boom():
        raise RuntimeError("secret PG password=sup3r PG SELECT * FROM documents path=/tmp/minio")

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/test-500-leak")
    assert resp.status_code == 500
    body = resp.json()
    assert body.get("detail") == "Erreur interne."
    # Ne doit pas contenir le message d'exception
    assert "sup3r" not in str(body).lower()
    assert "password" not in str(body).lower()
    assert "select" not in str(body).lower()
    assert "500test" not in str(body).lower()
    assert resp.headers.get("X-Request-ID")
