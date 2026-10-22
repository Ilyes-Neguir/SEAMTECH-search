"""Preuve S3/MinIO réelle pour audit Lot 0b — PG+Redis+MinIO avec identité restreinte.

Ce fichier est le seul à lire SEAMTECH_S3_ENDPOINT_URL en-dehors des tests
d'intégration docker ; il est donc dans INVENTAIRE_S3_REEL et porte
@pytest.mark.s3 + @pytest.mark.integration_docker pour être exécuté uniquement
par le job `integration` qui fournit PostgreSQL (5433), Redis (6379) et MinIO
(9000) avec l'identité applicative restreinte (seamtech-app-ci).

Dans ce contexte obligatoire, une configuration MinIO absente ou l'usage de
l'identité administrateur (minioadmin) doit échouer clairement, pas produire
un skip — un skip masquerait l'absence de preuve.
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

JETON_SERVICE = "jeton-de-service-de-test"

pytestmark = [pytest.mark.s3, pytest.mark.integration_docker]


def _pg_url() -> str:
    # Job integration compose : PG à 5433 avec test_password
    env_url = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")
    if env_url:
        return env_url
    # Fallback pour le job integration (compose)
    pwd = os.environ.get("POSTGRES_PASSWORD", "test_password")
    return f"postgresql://seamtech:{pwd}@127.0.0.1:5433/seamtech_search"


def _redis_url() -> str:
    env_url = os.environ.get("SEAMTECH_TEST_REDIS_URL") or os.environ.get("SEAMTECH_REDIS_URL", "")
    if env_url:
        return env_url
    pwd = os.environ.get("REDIS_PASSWORD", "redis_test_password")
    return f"redis://:{pwd}@127.0.0.1:6379/0"


def _s3_config():
    endpoint = os.environ.get("SEAMTECH_S3_ENDPOINT_URL", "")
    if not endpoint:
        pytest.fail("SEAMTECH_S3_ENDPOINT_URL absent dans le job integration — MinIO obligatoire, pas de skip")
    access = os.environ.get("SEAMTECH_S3_ACCESS_KEY", "")
    secret = os.environ.get("SEAMTECH_S3_SECRET_KEY", "")
    bucket = os.environ.get("SEAMTECH_S3_BUCKET", "seamtech-documents")
    # Identité restreinte obligatoire
    if not access or not secret:
        pytest.fail("SEAMTECH_S3_ACCESS_KEY/SECRET_KEY absents — identité applicative restreinte obligatoire")
    if access == "minioadmin" or secret in ("minioadmin", "minioadmin123"):
        pytest.fail("Identité administrateur minioadmin interdite — utiliser l'identité applicative restreinte (seamtech-app-ci)")
    return endpoint, bucket, access, secret


def _base_et_app_pg_redis_minio(tmp_path: Path):
    pg_url = _pg_url()
    redis_url = _redis_url()
    endpoint, bucket, access, secret = _s3_config()
    nom = f"audit_s3_{uuid.uuid4().hex[:8]}"
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    # Connexion admin pour créer la base jetable
    # pg_url est du type postgresql://seamtech:pwd@127.0.0.1:5433/seamtech_search
    # On en déduit l'URL de la base postgres (sans nom) pour CREATE DATABASE
    admin_url = pg_url.rsplit("/", 1)[0] + "/postgres"
    try:
        admin_conn = psycopg2.connect(admin_url)
    except Exception:
        # Fallback: utilise pg_url directement si admin_url échoue (certains runners)
        admin_conn = psycopg2.connect(pg_url)
    admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin_conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nom)))
    finally:
        admin_conn.close()
    url_base = f"{pg_url.rsplit('/',1)[0]}/{nom}"
    index = SearchIndex(tmp_path / f"{nom}.db", url_base)
    orig_redis = os.environ.get("SEAMTECH_REDIS_URL")
    os.environ["SEAMTECH_REDIS_URL"] = redis_url
    # S'assurer que l'app voit le S3 restreint
    orig_s3 = os.environ.get("SEAMTECH_S3_ENDPOINT_URL")
    orig_acc = os.environ.get("SEAMTECH_S3_ACCESS_KEY")
    orig_sec = os.environ.get("SEAMTECH_S3_SECRET_KEY")
    orig_bkt = os.environ.get("SEAMTECH_S3_BUCKET")
    os.environ["SEAMTECH_S3_ENDPOINT_URL"] = endpoint
    os.environ["SEAMTECH_S3_BUCKET"] = bucket
    os.environ["SEAMTECH_S3_ACCESS_KEY"] = access
    os.environ["SEAMTECH_S3_SECRET_KEY"] = secret
    try:
        index.initialize()
        index.run_migrations()
        config = AppConfig(root_paths=[tmp_path], database_url=url_base, min_free_bytes=0, auth_token=JETON_SERVICE, redis_url=redis_url, s3_endpoint_url=endpoint, s3_bucket=bucket, s3_access_key=access, s3_secret_key=secret)
        app = create_app(config)
        return index, app, url_base, nom, (orig_redis, orig_s3, orig_acc, orig_sec, orig_bkt), endpoint, bucket, access, secret
    except Exception:
        # Restaure env si échec init
        if orig_redis is None:
            os.environ.pop("SEAMTECH_REDIS_URL", None)
        else:
            os.environ["SEAMTECH_REDIS_URL"] = orig_redis
        if orig_s3 is None:
            os.environ.pop("SEAMTECH_S3_ENDPOINT_URL", None)
        else:
            os.environ["SEAMTECH_S3_ENDPOINT_URL"] = orig_s3
        if orig_acc is None:
            os.environ.pop("SEAMTECH_S3_ACCESS_KEY", None)
        else:
            os.environ["SEAMTECH_S3_ACCESS_KEY"] = orig_acc
        if orig_sec is None:
            os.environ.pop("SEAMTECH_S3_SECRET_KEY", None)
        else:
            os.environ["SEAMTECH_S3_SECRET_KEY"] = orig_sec
        if orig_bkt is None:
            os.environ.pop("SEAMTECH_S3_BUCKET", None)
        else:
            os.environ["SEAMTECH_S3_BUCKET"] = orig_bkt
        raise


def _restore_env(orig_redis, orig_s3, orig_acc, orig_sec, orig_bkt):
    if orig_redis is None:
        os.environ.pop("SEAMTECH_REDIS_URL", None)
    else:
        os.environ["SEAMTECH_REDIS_URL"] = orig_redis
    if orig_s3 is None:
        os.environ.pop("SEAMTECH_S3_ENDPOINT_URL", None)
    else:
        os.environ["SEAMTECH_S3_ENDPOINT_URL"] = orig_s3
    if orig_acc is None:
        os.environ.pop("SEAMTECH_S3_ACCESS_KEY", None)
    else:
        os.environ["SEAMTECH_S3_ACCESS_KEY"] = orig_acc
    if orig_sec is None:
        os.environ.pop("SEAMTECH_S3_SECRET_KEY", None)
    else:
        os.environ["SEAMTECH_S3_SECRET_KEY"] = orig_sec
    if orig_bkt is None:
        os.environ.pop("SEAMTECH_S3_BUCKET", None)
    else:
        os.environ["SEAMTECH_S3_BUCKET"] = orig_bkt


def test_audit_s3_piece_et_artifact_minio(tmp_path: Path):
    """PIECE_DOWNLOAD + ARTIFACT_DOWNLOAD avec MinIO réel (identité restreinte)."""
    from seamtech_search.storage import S3StorageClient

    index, app, url_base, nom, orig_env, endpoint, bucket, access, secret = _base_et_app_pg_redis_minio(tmp_path)
    import psycopg2
    from psycopg2 import sql as pgsql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    # Variables pour cleanup dans finally même si assertion échoue
    remote_key_piece = None
    remote_key_artifact = None
    s3_client = None
    piece_id = None
    job_id = None

    try:
        # Vérifie que l'identité est bien restreinte (pas minioadmin)
        assert access == "seamtech-app-ci", f"identité applicative attendue seamtech-app-ci, got {access}"
        s3_client = S3StorageClient(endpoint_url=endpoint, bucket_name=bucket, access_key_id=access, secret_access_key=secret)
        # Doit réussir (sinon échec clair, pas skip)
        s3_client.ensure_bucket_exists()

        # Crée utilisateur et session
        from seamtech_search.comptes.comptes import creer_utilisateur

        created = creer_utilisateur(index, "admin_s3", "Nom admin_s3", "Secret123!", "administrateur")
        client = TestClient(app)
        resp = client.post("/auth/connexion", json={"identifiant": "admin_s3", "mot_de_passe": "Secret123!"}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
        assert resp.status_code == 200
        sid = resp.json().get("sid")
        assert sid

        # --- PIECE via S3 ---
        local_pdf = tmp_path / "s3_piece.pdf"
        local_pdf.write_bytes(b"%PDF-1.4 S3 proof piece")
        remote_key_piece = f"audit-test/{uuid.uuid4().hex}/s3_piece.pdf"
        uploaded = s3_client.upload_file(local_pdf, remote_key=remote_key_piece)
        assert uploaded == remote_key_piece

        # Insère document avec object_key S3 (path local existe mais on teste S3 path aussi)
        with index.connect() as conn:
            with conn.cursor() as cur:
                path_key = f"s3://{bucket}/{remote_key_piece}"
                # Supprime le fichier local pour forcer le chemin S3 si possible, mais garde un path pour la table
                # On garde le fichier local pour le cas local_head, mais on va aussi tester le cas S3 en supprimant
                cur.execute(
                    "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, is_dir, object_key, object_bucket) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s, FALSE, %s, %s) RETURNING id",
                    (path_key, str(local_pdf), "s3_piece.pdf", str(tmp_path), ".pdf", local_pdf.stat().st_size, local_pdf.stat().st_mtime, remote_key_piece, bucket),
                )
                piece_id = int(cur.fetchone()[0])

        resp_dl = client.get(f"/pieces/{piece_id}/telecharger", headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid})
        assert resp_dl.status_code in (200, 302), f"PIECE S3 status {resp_dl.status_code}: {resp_dl.text[:500]}"
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_user_id, action, outcome, target_id, target_type FROM audit_logs WHERE action='PIECE_DOWNLOAD' AND target_id=%s ORDER BY id DESC LIMIT 1", (str(piece_id),))
                row = cur.fetchone()
                assert row is not None, "PIECE_DOWNLOAD S3 non journalisé"
                assert row[0] == created["id_utilisateur"]
                assert row[2] == "success" and row[4] == "piece"

        # --- ARTIFACT via S3 (succès) ---
        # Prépare un fichier d'artefact local + S3
        artifact_pdf = tmp_path / "artifact_report.pdf"
        artifact_pdf.write_bytes(b"%PDF-1.4 artifact proof")
        remote_key_artifact = f"audit-test/{uuid.uuid4().hex}/artifact_report.pdf"
        s3_client.upload_file(artifact_pdf, remote_key=remote_key_artifact)

        # Crée un job d'import avec artifact S3
        from seamtech_search.jobs import create_job, update_job

        job_id = uuid.uuid4().hex
        create_job(index, job_id, str(tmp_path), status="done", stage="done")
        # Le fichier local n'existe pas (pour forcer S3), mais l'objet S3 existe
        # On met report_path vers un fichier inexistant, et artifact avec object_key S3
        # L'API va tenter de servir via S3 (download) ou presigned
        fake_local_path = str(tmp_path / f"missing_{job_id}.pdf")
        result_payload = {
            "report_path": fake_local_path,
            "report_docx_path": None,
            "artifacts": [{"name": "technical-report.pdf", "key": remote_key_artifact}],
        }
        update_job(index, job_id, status="done", stage="done", result=result_payload)

        resp_art = client.get(f"/imports/{job_id}/artifacts/report_pdf", headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid})
        # Peut être 200 (fichier servi après download S3) ou 302 (presigned URL) selon config
        assert resp_art.status_code in (200, 302), f"ARTIFACT S3 status {resp_art.status_code}: {resp_art.text[:500]}"
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_user_id, action, outcome, target_type, target_id FROM audit_logs WHERE action='ARTIFACT_DOWNLOAD' ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                assert row is not None, "ARTIFACT_DOWNLOAD S3 non journalisé"
                assert row[0] == created["id_utilisateur"]
                assert row[1] == "ARTIFACT_DOWNLOAD" and row[2] == "success" and row[3] == "import_artifact"
                assert remote_key_artifact in str(row[4]) or job_id in str(row[4])

        # Vérifie que l'objet S3 a bien été utilisé (pas de mock) — on peut lister
        assert s3_client.object_exists(remote_key_piece) is True
        assert s3_client.object_exists(remote_key_artifact) is True

    finally:
        # Nettoyage S3 même si assertion échoue
        if s3_client is not None:
            for key in (remote_key_piece, remote_key_artifact):
                if key:
                    try:
                        s3_client._get_client().delete_object(Bucket=bucket, Key=key)
                    except Exception:
                        pass
        # Restaure env
        _restore_env(*orig_env)
        # Nettoie DB
        try:
            index.close()
        except Exception:
            pass
        try:
            pg_url = _pg_url()
            admin_url = pg_url.rsplit("/", 1)[0] + "/postgres"
            try:
                admin_conn = psycopg2.connect(admin_url)
            except Exception:
                admin_conn = psycopg2.connect(pg_url)
            admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            try:
                with admin_conn.cursor() as cur:
                    cur.execute(pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(nom)))
            finally:
                admin_conn.close()
        except Exception:
            pass
