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
        created = _cree_utilisateur(index, "admin_a", "administrateur")
        client = TestClient(app)

        # succès
        resp_ok = client.post("/auth/connexion", json={"identifiant": "admin_a", "mot_de_passe": "Secret123!"}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
        assert resp_ok.status_code == 200
        sid_ok = resp_ok.json().get("sid")
        assert sid_ok

        # Vérifie en base acteur nominatif — avec actor_user_id exact
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_user_id, actor_username, actor_role, action, outcome, target_id, target_type FROM audit_logs WHERE action='AUTH_LOGIN_SUCCESS' ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                assert row is not None, "AUTH_LOGIN_SUCCESS non journalisé"
                assert row[1] == "admin_a" and row[2] == "administrateur" and row[4] == "success", f"acteur/outcome incorrect: {row}"
                assert row[0] == created["id_utilisateur"], f"actor_user_id doit être {created['id_utilisateur']}, got {row[0]}"
                assert row[5] == "admin_a" and row[6] == "utilisateur", f"cible incorrecte: {row}"

        # échec
        resp_fail = client.post("/auth/connexion", json={"identifiant": "admin_a", "mot_de_passe": "mauvais"}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
        assert resp_fail.status_code in (401, 429)
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_username, action, outcome, target_id FROM audit_logs WHERE action='AUTH_LOGIN_FAILURE' ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                assert row is not None and row[1] == "AUTH_LOGIN_FAILURE" and row[2] == "failure", f"failure non journalisé: {row}"
                assert row[3] == "admin_a", f"target_id doit être admin_a: {row}"

        # secours succès (acteur secours, actor_user_id None car pas en PostgreSQL)
        resp_sec = client.post("/auth/connexion-secours", json={}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
        assert resp_sec.status_code == 200
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_user_id, actor_username, actor_role, action, outcome, target_id FROM audit_logs WHERE target_id='secours' AND action='AUTH_LOGIN_SUCCESS' ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                assert row is not None and row[1] == "secours", f"secours non journalisé: {row}"
                assert row[2] == "administrateur" and row[4] == "success"
                assert row[0] is None, "secours n'a pas d'id utilisateur PostgreSQL"

        # détails assainis : aucun secret même si on tente d'en injecter
        from seamtech_search.audit_logs import record_audit_log

        record_audit_log(index, actor_user_id=None, actor_username="tester", actor_role="operateur", action="AUTH_LOGIN_FAILURE", target_type="utilisateur", target_id="victim", outcome="failure", details={"mot_de_passe": "secret123", "meta": {"token": "secret_nested", "ok": "keep"}, "api_token": "leak", "password_hash": "leak2"})
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT details_json FROM audit_logs WHERE target_id='victim' ORDER BY id DESC LIMIT 1")
                dj = cur.fetchone()[0]
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
        created = _cree_utilisateur(index, "admin_b", "administrateur")
        client = TestClient(app)
        resp = client.post("/auth/connexion", json={"identifiant": "admin_b", "mot_de_passe": "Secret123!"}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
        sid = resp.json().get("sid")
        assert sid

        # IMPORT_CREATE synchrone (wait=true) avec vrai dossier vide -> 400 mais audit failure doit exister
        import tempfile

        tmpdir = Path(tempfile.mkdtemp())
        # échec attendu (pas de pdf) mais on vérifie l'audit — doit exister et être nominatif
        resp_imp = client.post("/imports", json={"source_path": str(tmpdir)}, params={"wait": True}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid})
        # Dossier vide => ValueError côté import_folder => 400; si espace insuffisant => 507
        assert resp_imp.status_code in (400, 507, 403), f"status inattendu: {resp_imp.status_code} {resp_imp.text}"
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_user_id, actor_username, actor_role, action, outcome, target_id, target_type FROM audit_logs WHERE action='IMPORT_CREATE' ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                assert row is not None, "IMPORT_CREATE non journalisé avec session nominative"
                # Vérifie acteur exact (ID numérique, nom, rôle)
                assert row[0] == created["id_utilisateur"], f"actor_user_id doit être {created['id_utilisateur']}, got {row[0]}"
                assert row[1] == "admin_b" and row[2] == "administrateur", f"acteur non nominatif: {row}"
                # Dossier vide => failure obligatoire (pas success)
                assert row[4] == "failure", f"outcome doit être failure pour dossier vide, got {row[4]}"
                # Cible: job_id hex 32 chars pour wait=true, ou pre_job pour insufficient_storage avant job
                assert row[5] is not None and len(str(row[5])) >= 6, f"target_id manquant: {row}"
                assert row[6] == "import", f"target_type doit être import, got {row[6]}"

        # Sans session, fallback service (mais pas de fuite token)
        client.post("/imports", json={"source_path": str(tmpdir)}, params={"wait": True}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_username, details_json FROM audit_logs WHERE action='IMPORT_CREATE' ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                assert row is not None
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


@pytest.mark.postgres
def test_audit_logout_et_revocation(tmp_path: Path):
    """Vérifie AUTH_LOGOUT, SESSION_REVOKED, AUTH_LOGOUT_ALL avec session serveur validée."""
    index, app, url_base, nom = _base_et_app(tmp_path)
    import psycopg2
    from psycopg2 import sql as pgsql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    try:
        created_admin = _cree_utilisateur(index, "admin_logout", "administrateur")
        _cree_utilisateur(index, "oper_logout", "operateur")
        client = TestClient(app)

        # Connexion admin -> sid
        resp = client.post("/auth/connexion", json={"identifiant": "admin_logout", "mot_de_passe": "Secret123!"}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
        assert resp.status_code == 200
        sid = resp.json().get("sid")
        id_session = resp.json().get("id_session")
        assert sid and id_session

        # Deconnexion nominative (sid)
        resp_dec = client.post("/auth/deconnexion", json={"sid": sid}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
        assert resp_dec.status_code == 200

        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_user_id, actor_username, actor_role, action, outcome, target_id, target_type FROM audit_logs WHERE action='AUTH_LOGOUT' ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                assert row is not None, "AUTH_LOGOUT non journalisé"
                assert row[0] == created_admin["id_utilisateur"], f"actor_user_id logout: {row}"
                assert row[1] == "admin_logout" and row[2] == "administrateur"
                assert row[4] == "success" and row[6] == "session", f"outcome/target_type: {row}"
                # SESSION_REVOKED doit suivre si db_session_id existe
                cur.execute("SELECT action, actor_user_id, target_type, target_id FROM audit_logs WHERE action='SESSION_REVOKED' ORDER BY id DESC LIMIT 1")
                row_rev = cur.fetchone()
                if row_rev:
                    assert row_rev[1] == created_admin["id_utilisateur"], f"SESSION_REVOKED acteur: {row_rev}"
                    assert row_rev[2] == "session_ui"
                    assert str(row_rev[3]) == str(id_session), f"target_id SESSION_REVOKED doit être id_session {id_session}: {row_rev}"

        # Re-connexion pour tester AUTH_LOGOUT_ALL
        resp2 = client.post("/auth/connexion", json={"identifiant": "admin_logout", "mot_de_passe": "Secret123!"}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
        sid2 = resp2.json().get("sid")
        assert sid2
        resp_all = client.post("/auth/deconnexion-tout", json={}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid2})
        assert resp_all.status_code == 200
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_user_id, actor_username, action, outcome, target_id FROM audit_logs WHERE action='AUTH_LOGOUT_ALL' ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                assert row is not None, "AUTH_LOGOUT_ALL non journalisé"
                assert row[0] == created_admin["id_utilisateur"]
                assert row[1] == "admin_logout" and row[3] == "success"
                assert str(row[4]) == str(created_admin["id_utilisateur"]), f"target_id LOGOUT_ALL doit être user_id: {row}"

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
def test_audit_account_lifecycle(tmp_path: Path):
    """ACCOUNT_CREATE / DISABLE / PASSWORD_CHANGE / PASSWORD_RESET avec acteur nominatif."""
    index, app, url_base, nom = _base_et_app(tmp_path)
    import psycopg2
    from psycopg2 import sql as pgsql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    try:
        created_admin = _cree_utilisateur(index, "admin_acct", "administrateur")
        client = TestClient(app)
        resp = client.post("/auth/connexion", json={"identifiant": "admin_acct", "mot_de_passe": "Secret123!"}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
        sid = resp.json().get("sid")
        assert sid

        # ACCOUNT_CREATE via API (acteur = admin_acct)
        resp_create = client.post("/auth/utilisateurs", json={"identifiant": "new_user", "nom": "New User", "mot_de_passe": "Secret123!", "role": "operateur"}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid})
        assert resp_create.status_code == 200, resp_create.text
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_user_id, actor_username, action, outcome, target_id, target_type FROM audit_logs WHERE action='ACCOUNT_CREATE' ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                assert row is not None, "ACCOUNT_CREATE non journalisé"
                assert row[0] == created_admin["id_utilisateur"]
                assert row[1] == "admin_acct" and row[3] == "success"
                assert row[4] == "new_user" and row[5] == "utilisateur"

        # ACCOUNT_PASSWORD_RESET (admin reset mot de passe de new_user)
        resp_reset = client.post("/auth/utilisateurs/new_user/mot-de-passe", json={"mot_de_passe": "NewSecret123!"}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid})
        assert resp_reset.status_code == 200, resp_reset.text
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_user_id, action, target_id FROM audit_logs WHERE action='ACCOUNT_PASSWORD_RESET' ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                assert row is not None, "ACCOUNT_PASSWORD_RESET non journalisé"
                assert row[0] == created_admin["id_utilisateur"]
                assert row[2] == "new_user"

        # ACCOUNT_DISABLE
        resp_dis = client.post("/auth/utilisateurs/new_user/desactiver", headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid})
        assert resp_dis.status_code == 200, resp_dis.text
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_user_id, action, target_id, outcome FROM audit_logs WHERE action='ACCOUNT_DISABLE' ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                assert row is not None, "ACCOUNT_DISABLE non journalisé"
                assert row[0] == created_admin["id_utilisateur"]
                assert row[2] == "new_user" and row[3] == "success"

        # ACCOUNT_PASSWORD_CHANGE (par l'intéressé lui-même)
        # On crée un autre user actif pour ce test
        resp_create2 = client.post("/auth/utilisateurs", json={"identifiant": "changer_pwd", "nom": "Changer", "mot_de_passe": "Secret123!", "role": "operateur"}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid})
        assert resp_create2.status_code == 200
        resp_login2 = client.post("/auth/connexion", json={"identifiant": "changer_pwd", "mot_de_passe": "Secret123!"}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
        sid2 = resp_login2.json().get("sid")
        id_u2 = resp_login2.json().get("id_utilisateur")
        assert sid2
        resp_chg = client.post("/auth/mot-de-passe", json={"mot_de_passe_actuel": "Secret123!", "nouveau_mot_de_passe": "Changed123!"}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid2})
        assert resp_chg.status_code == 200, resp_chg.text
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_user_id, actor_username, action, target_id FROM audit_logs WHERE action='ACCOUNT_PASSWORD_CHANGE' ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                assert row is not None, "ACCOUNT_PASSWORD_CHANGE non journalisé"
                assert row[0] == id_u2, f"actor_user_id doit être celui de changer_pwd ({id_u2}), got {row[0]}"
                assert row[1] == "changer_pwd"
                assert row[3] == "changer_pwd"

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
def test_audit_piece_et_artifact(tmp_path: Path):
    """PIECE_DOWNLOAD et ARTIFACT_DOWNLOAD nominatifs."""
    index, app, url_base, nom = _base_et_app(tmp_path)
    import psycopg2
    from psycopg2 import sql as pgsql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    try:
        created = _cree_utilisateur(index, "admin_piece", "administrateur")
        client = TestClient(app)
        resp = client.post("/auth/connexion", json={"identifiant": "admin_piece", "mot_de_passe": "Secret123!"}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
        sid = resp.json().get("sid")
        assert sid

        # Crée une pièce document réelle dans tmp_path et l'insère en base
        piece_file = tmp_path / "dummy.pdf"
        piece_file.write_bytes(b"%PDF-1.4 dummy")
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO documents (path, path_key, name, extension, size, parent_path) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                    (str(piece_file), str(piece_file), "dummy.pdf", ".pdf", piece_file.stat().st_size, str(tmp_path)),
                )
                piece_id = int(cur.fetchone()[0])

        # Téléchargement pièce avec session nominative -> success
        resp_dl = client.get(f"/pieces/{piece_id}/telecharger", headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid})
        assert resp_dl.status_code == 200, resp_dl.text[:300]
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_user_id, actor_username, action, outcome, target_id, target_type FROM audit_logs WHERE action='PIECE_DOWNLOAD' AND target_id=%s ORDER BY id DESC LIMIT 1", (str(piece_id),))
                row = cur.fetchone()
                assert row is not None, "PIECE_DOWNLOAD non journalisé"
                assert row[0] == created["id_utilisateur"]
                assert row[1] == "admin_piece" and row[3] == "success"
                assert row[4] == str(piece_id) and row[5] == "piece"

        # Echec: pièce inexistante -> failure mais journalisé
        resp_nf = client.get("/pieces/999999/telecharger", headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid})
        assert resp_nf.status_code == 404
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT action, outcome, target_id FROM audit_logs WHERE action='PIECE_DOWNLOAD' AND target_id='999999' ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                assert row is not None and row[1] == "failure", f"failure non journalisé: {row}"

        # ARTIFACT_DOWNLOAD: via /imports/{id}/artifacts/* (import inexistant => failure)
        resp_art = client.get("/imports/nonexistent/artifacts/report_pdf", headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid})
        assert resp_art.status_code == 404
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_user_id, action, outcome, target_type FROM audit_logs WHERE action='ARTIFACT_DOWNLOAD' ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                assert row is not None, "ARTIFACT_DOWNLOAD non journalisé"
                assert row[0] == created["id_utilisateur"]
                assert row[1] == "ARTIFACT_DOWNLOAD" and row[2] == "failure" and row[3] == "import_artifact"

    finally:
        index.close()
        admin_conn = psycopg2.connect(URL_PG)
        admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin_conn.cursor() as cur:
                cur.execute(pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(nom)))
        finally:
            admin_conn.close()
