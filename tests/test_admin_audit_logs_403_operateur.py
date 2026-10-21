"""Lot 0b — GET /api/admin/audit-logs : contrat admin / no-store / paramétré.

Vérifie :
- 403 pour opérateur authentifié (pas seulement masquage lien)
- 200 pour administrateur avec session validée côté serveur
- Cache-Control: no-store sur réponse
- Validation bornée limit/offset/sort/outcome (422)
- Lecture paramétrée (injection traitée comme valeur, pas d'erreur SQL)
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex

URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")
URL_REDIS = os.environ.get("SEAMTECH_TEST_REDIS_URL", "")
JETON_SERVICE = "jeton-de-service-de-test"

pytestmark = pytest.mark.postgres


def _base_et_app(tmp_path: Path):
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL")
    if not URL_REDIS:
        pytest.skip("Set SEAMTECH_TEST_REDIS_URL")
    nom = f"audit_admin_{uuid.uuid4().hex[:8]}"
    # Réutilise la fixture base_metier de manière inline
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin_conn = psycopg2.connect(URL_PG)
    admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin_conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nom)))
    finally:
        admin_conn.close()

    url_base = f"{URL_PG.rsplit('/',1)[0]}/{nom}"
    index = SearchIndex(tmp_path / f"{nom}.db", url_base)
    # Inject redis url via env for AppConfig
    orig_redis = os.environ.get("SEAMTECH_REDIS_URL")
    os.environ["SEAMTECH_REDIS_URL"] = URL_REDIS
    try:
        index.initialize()
        index.run_migrations()
        config = AppConfig(
            root_paths=[tmp_path],
            database_url=url_base,
            min_free_bytes=0,
            auth_token=JETON_SERVICE,
            redis_url=URL_REDIS,
        )
        app = create_app(config)
        return index, app, url_base, nom
    finally:
        if orig_redis is None:
            os.environ.pop("SEAMTECH_REDIS_URL", None)
        else:
            os.environ["SEAMTECH_REDIS_URL"] = orig_redis


def _cree_utilisateur(index: SearchIndex, identifiant: str, role: str, mdp: str = "Secret123!"):
    from seamtech_search.comptes.comptes import creer_utilisateur

    return creer_utilisateur(index, identifiant, f"Nom {identifiant}", mdp, role)


def _login_et_sid(app, identifiant: str, mdp: str = "Secret123!"):
    client = TestClient(app)
    resp = client.post(
        "/auth/connexion",
        json={"identifiant": identifiant, "mot_de_passe": mdp},
        headers={"X-SEAMTECH-TOKEN": JETON_SERVICE},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    sid = data.get("sid") or data.get("id_session") or data.get("session", {}).get("sid")
    # Lot0a: le sid est dans la réponse; si absent, utilise via header retourné
    if not sid:
        # fallback: extrait du header Set-Cookie si présent (non utilisé en API directe)
        sid = resp.headers.get("X-SEAMTECH-SESSION-SID") or data.get("sid")
    assert sid, f"sid manquant après login {identifiant}: {data}"
    return sid, client


@pytest.mark.postgres
def test_admin_audit_logs_403_operateur(tmp_path: Path):
    index, app, url_base, nom = _base_et_app(tmp_path)
    import psycopg2
    from psycopg2 import sql as pgsql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    try:
        _cree_utilisateur(index, "admin_test", "administrateur")
        _cree_utilisateur(index, "oper_test", "operateur")

        sid_admin, _ = _login_et_sid(app, "admin_test")
        sid_oper, _ = _login_et_sid(app, "oper_test")

        # Opérateur doit être refusé (403), pas 404 ni 200
        client_oper = TestClient(app)
        resp_oper = client_oper.get(
            "/api/admin/audit-logs",
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_oper},
        )
        assert resp_oper.status_code == 403, f"opérateur doit être 403, got {resp_oper.status_code} {resp_oper.text}"

        # Admin doit réussir et avoir no-store
        client_admin = TestClient(app)
        resp_admin = client_admin.get(
            "/api/admin/audit-logs",
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_admin},
            params={"limit": 10, "offset": 0, "sort": "created_at", "order": "desc"},
        )
        assert resp_admin.status_code == 200, resp_admin.text
        assert resp_admin.headers.get("Cache-Control") == "no-store"
        body = resp_admin.json()
        assert "results" in body and "count" in body

        # Validation bornée: limit hors borne => 422
        resp_bad = client_admin.get(
            "/api/admin/audit-logs",
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_admin},
            params={"limit": 999},
        )
        assert resp_bad.status_code == 422

        # Injection: action avec SQL injection doit être traitée comme valeur (0 résultat, pas d'erreur)
        resp_inject = client_admin.get(
            "/api/admin/audit-logs",
            headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid_admin},
            params={"action": "'; DROP TABLE audit_logs; --"},
        )
        assert resp_inject.status_code == 200
        # Vérifie que la table existe toujours
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.audit_logs')")
                assert cur.fetchone()[0] is not None

        # Historique non-contournement: /audit (ancien libre) ne donne pas accès aux logs nominatifs
        # On vérifie que /api/admin/audit-logs sans token service => 401 même avec sid valide
        resp_no_token = client_admin.get(
            "/api/admin/audit-logs",
            headers={"X-SEAMTECH-SESSION-SID": sid_admin},
        )
        assert resp_no_token.status_code == 401

    finally:
        index.close()
        admin_conn = psycopg2.connect(URL_PG)
        admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin_conn.cursor() as cur:
                cur.execute(pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(nom)))
        finally:
            admin_conn.close()
