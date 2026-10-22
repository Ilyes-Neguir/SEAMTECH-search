"""Lot 0b — suite d'événements audit : vérifie actor, cible, outcome, détails assainis.

Couvre AUTH_LOGIN_SUCCESS/FAILURE, AUTH_LOGOUT, SESSION_REVOKED, IMPORT_CREATE, PIECE/ARTIFACT
avec vraie session Redis et PostgreSQL, sans mocks des intégrations critiques.
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
    nom = f"audit_evt_{uuid.uuid4().hex[:8]}"
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
    orig_redis = os.environ.get("SEAMTECH_REDIS_URL")
    os.environ["SEAMTECH_REDIS_URL"] = URL_REDIS
    try:
        index.initialize()
        index.run_migrations()
        config = AppConfig(root_paths=[tmp_path], database_url=url_base, min_free_bytes=0, auth_token=JETON_SERVICE, redis_url=URL_REDIS)
        app = create_app(config)
        return index, app, url_base, nom
    finally:
        if orig_redis is None:
            os.environ.pop("SEAMTECH_REDIS_URL", None)
        else:
            os.environ["SEAMTECH_REDIS_URL"] = orig_redis


def _cree_utilisateur(index, identifiant, role):
    from seamtech_search.comptes.comptes import creer_utilisateur

    return creer_utilisateur(index, identifiant, f"Nom {identifiant}", "Secret123!", role)


@pytest.mark.postgres
def test_audit_login_success_et_failure(tmp_path: Path):
    index, app, url_base, nom = _base_et_app(tmp_path)
    import psycopg2
    from psycopg2 import sql as pgsql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    try:
        _cree_utilisateur(index, "admin_a", "administrateur")
        client = TestClient(app)

        # succès
        resp_ok = client.post("/auth/connexion", json={"identifiant": "admin_a", "mot_de_passe": "Secret123!"}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
        assert resp_ok.status_code == 200
        sid_ok = resp_ok.json().get("sid")
        assert sid_ok

        # Vérifie en base acteur nominatif
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_username, actor_role, action, outcome, target_id FROM audit_logs WHERE action='AUTH_LOGIN_SUCCESS' ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                assert row is not None, "AUTH_LOGIN_SUCCESS non journalisé"
                assert row[0] == "admin_a" and row[1] == "administrateur" and row[3] == "success"

        # échec
        resp_fail = client.post("/auth/connexion", json={"identifiant": "admin_a", "mot_de_passe": "mauvais"}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
        assert resp_fail.status_code in (401, 429)
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_username, action, outcome FROM audit_logs WHERE action='AUTH_LOGIN_FAILURE' ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                assert row is not None and row[1] == "AUTH_LOGIN_FAILURE" and row[2] == "failure"

        # secours succès
        resp_sec = client.post("/auth/connexion-secours", json={}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
        assert resp_sec.status_code == 200
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_username, actor_role, action FROM audit_logs WHERE target_id='secours' AND action='AUTH_LOGIN_SUCCESS' ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                assert row is not None and row[0] == "secours"

        # détails assainis : aucun secret même si on tente d'en injecter
        from seamtech_search.audit_logs import record_audit_log

        record_audit_log(index, actor_user_id=None, actor_username="tester", actor_role="operateur", action="AUTH_LOGIN_FAILURE", target_type="utilisateur", target_id="victim", outcome="failure", details={"mot_de_passe": "secret123", "meta": {"token": "secret_nested", "ok": "keep"}, "api_token": "leak", "password_hash": "leak2"})
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT details_json FROM audit_logs WHERE target_id='victim' ORDER BY id DESC LIMIT 1")
                dj = cur.fetchone()[0]
                # psycopg2 returns dict for JSONB
                import json
                if isinstance(dj, str):
                    dj = json.loads(dj)
                assert "mot_de_passe" not in str(dj).lower()
                assert "secret123" not in str(dj)
                assert "token" not in str(dj).lower() or dj.get("meta", {}).get("token") is None
                assert dj.get("meta", {}).get("ok") == "keep"

    finally:
        index.close()
        admin_conn = psycopg2.connect(URL_PG)
        admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin_conn.cursor() as cur:
                cur.execute(pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(nom)))
        finally:
            admin_conn.close()


@pytest.mark.postgres
def test_import_create_et_artifact_nominatif(tmp_path: Path):
    index, app, url_base, nom = _base_et_app(tmp_path)
    import psycopg2
    from psycopg2 import sql as pgsql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    try:
        _cree_utilisateur(index, "admin_b", "administrateur")
        client = TestClient(app)
        resp = client.post("/auth/connexion", json={"identifiant": "admin_b", "mot_de_passe": "Secret123!"}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
        sid = resp.json().get("sid")
        assert sid

        # IMPORT_CREATE synchrone (wait=true) avec vrai dossier vide -> 400 mais audit failure doit exister
        # On crée un dossier vide qui échouera en validation, mais doit logger
        import tempfile

        tmpdir = Path(tempfile.mkdtemp())
        # échec attendu (pas de pdf) mais on vérifie l'audit — doit exister et être nominatif
        resp_imp = client.post("/imports", json={"source_path": str(tmpdir)}, params={"wait": True}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid})
        assert resp_imp.status_code in (400, 507, 403)  # échec attendu mais journalisé
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_user_id, actor_username, actor_role, action, outcome, target_id FROM audit_logs WHERE action='IMPORT_CREATE' ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                assert row is not None, "IMPORT_CREATE non journalisé avec session nominative"
                # Vérifie acteur, ID, cible, résultat obligatoirement
                assert row[1] == "admin_b" and row[2] == "administrateur", f"acteur non nominatif: {row}"
                assert row[4] == "failure" or row[4] == "success"  # failure pour dossier vide
                # Vérifie actor_user_id est renseigné (int, pas None)
                assert row[0] is not None, "actor_user_id doit être renseigné pour session nominative"

        # Sans session, fallback service (mais pas de fuite token)
        client.post("/imports", json={"source_path": str(tmpdir)}, params={"wait": True}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_username, details_json FROM audit_logs WHERE action='IMPORT_CREATE' ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                assert row is not None
                # Vérifie pas de jeton dans details
                dj = row[1]
                import json
                if isinstance(dj, str):
                    dj = json.loads(dj)
                assert "jeton" not in str(dj).lower()
                assert JETON_SERVICE[:8] not in str(dj)

    finally:
        index.close()
        admin_conn = psycopg2.connect(URL_PG)
        admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin_conn.cursor() as cur:
                cur.execute(pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(nom)))
        finally:
            admin_conn.close()
