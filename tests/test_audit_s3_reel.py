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
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex

JETON_SERVICE = "jeton-de-service-de-test"

pytestmark = [pytest.mark.s3, pytest.mark.integration_docker, pytest.mark.postgres]


def _wait_for_postgres(url: str, timeout: int = 15) -> bool:
    import psycopg2

    start = time.time()
    while time.time() - start < timeout:
        try:
            conn = psycopg2.connect(url, connect_timeout=2)
            conn.close()
            return True
        except Exception:
            time.sleep(0.5)
    return False


def _wait_for_redis(url: str, timeout: int = 10) -> bool:
    try:
        import redis

        r = redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        start = time.time()
        while time.time() - start < timeout:
            try:
                if r.ping():
                    return True
            except Exception:
                pass
            time.sleep(0.5)
    except Exception:
        pass
    return False


def _pg_url() -> str:
    # Job integration compose : PG à 5433 avec test_password (cf docker-compose.yml)
    # On essaie plusieurs variantes comme test_integration_docker
    candidates = []
    for env_key in ("SEAMTECH_TEST_DATABASE_URL", "SEAMTECH_DATABASE_URL", "SEAMTECH_E2E_DATABASE_URL"):
        v = os.environ.get(env_key)
        if v:
            candidates.append(v)
    pwd = os.environ.get("POSTGRES_PASSWORD", "test_password")
    candidates.extend(
        [
            f"postgresql://seamtech:{pwd}@127.0.0.1:5433/seamtech_search",
            f"postgresql://seamtech:{pwd}@127.0.0.1:5432/seamtech_search",
            f"postgresql://seamtech:{pwd}@127.0.0.1:5432/seamtech_search_test",
        ]
    )
    for url in candidates:
        if _wait_for_postgres(url, timeout=10):
            return url
    # Fallback strict : doit échouer clairement, pas skip
    if os.environ.get("SEAMTECH_INTEGRATION_STRICT") in {"1", "true", "yes"}:
        pytest.fail(f"PostgreSQL non joignable pour preuve S3 (candidates: {candidates})")
    return candidates[0]


def _redis_url() -> str:
    for k in ("SEAMTECH_REDIS_URL", "SEAMTECH_TEST_REDIS_URL"):
        v = os.environ.get(k)
        if v and _wait_for_redis(v, timeout=2):
            return v
    pwd = os.environ.get("REDIS_PASSWORD", "redis_test_password")
    candidates = [
        f"redis://:{pwd}@127.0.0.1:6379/0",
        "redis://127.0.0.1:6379/0",
        "redis://:redis_test_password@127.0.0.1:6379/0",
    ]
    for url in candidates:
        if _wait_for_redis(url, timeout=10):
            return url
    if os.environ.get("SEAMTECH_INTEGRATION_STRICT") in {"1", "true", "yes"}:
        pytest.fail(f"Redis non joignable pour preuve S3 (candidates: {candidates})")
    return candidates[0]


def _s3_config():
    endpoint = os.environ.get("SEAMTECH_S3_ENDPOINT_URL", "")
    if not endpoint:
        pytest.fail("SEAMTECH_S3_ENDPOINT_URL absent dans le job integration — MinIO obligatoire, pas de skip")
    access = os.environ.get("SEAMTECH_S3_ACCESS_KEY", "")
    secret = os.environ.get("SEAMTECH_S3_SECRET_KEY", "")
    bucket = os.environ.get("SEAMTECH_S3_BUCKET", "seamtech-documents")
    if not access or not secret:
        pytest.fail("SEAMTECH_S3_ACCESS_KEY/SECRET_KEY absents — identité applicative restreinte obligatoire")
    if access == "minioadmin" or secret in ("minioadmin", "minioadmin123"):
        pytest.fail("Identité administrateur minioadmin interdite — utiliser l'identité applicative restreinte (seamtech-app-ci)")
    return endpoint, bucket, access, secret


def _base_et_app_pg_redis_minio(tmp_path: Path):
    # Réutilise la base `seamtech_search` existante (comme test_integration_docker)
    # pour éviter CREATE DATABASE qui nécessite superuser et peut échouer si
    # le rôle `seamtech` n'a pas droit de créer une DB dans le compose.
    pg_url = _pg_url()
    redis_url = _redis_url()
    endpoint, bucket, access, secret = _s3_config()
    nom = "seamtech_search"  # base existante, pas de création jetable
    index = SearchIndex(tmp_path / f"audit_s3_{uuid.uuid4().hex[:8]}.db", pg_url)
    orig_env = {
        k: os.environ.get(k)
        for k in ("SEAMTECH_REDIS_URL", "SEAMTECH_S3_ENDPOINT_URL", "SEAMTECH_S3_BUCKET", "SEAMTECH_S3_ACCESS_KEY", "SEAMTECH_S3_SECRET_KEY")
    }
    os.environ["SEAMTECH_REDIS_URL"] = redis_url
    os.environ["SEAMTECH_S3_ENDPOINT_URL"] = endpoint
    os.environ["SEAMTECH_S3_BUCKET"] = bucket
    os.environ["SEAMTECH_S3_ACCESS_KEY"] = access
    os.environ["SEAMTECH_S3_SECRET_KEY"] = secret
    try:
        index.initialize()
        index.run_migrations()
    except Exception as e:
        for k, v in orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        pytest.fail(f"SearchIndex init/migrations échoué: {e}")
    config = AppConfig(
        root_paths=[tmp_path],
        database_url=pg_url,
        min_free_bytes=0,
        auth_token=JETON_SERVICE,
        redis_url=redis_url,
        s3_endpoint_url=endpoint,
        s3_bucket=bucket,
        s3_access_key=access,
        s3_secret_key=secret,
    )
    app = create_app(config)
    return index, app, pg_url, nom, orig_env, endpoint, bucket, access, secret


def _restore_env(orig_env: dict):
    for k, v in orig_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_audit_s3_piece_et_artifact_minio(tmp_path: Path):
    """PIECE_DOWNLOAD + ARTIFACT_DOWNLOAD avec MinIO réel — vérifie octets + audit complet."""
    from seamtech_search.storage import S3StorageClient

    index, app, url_base, nom, orig_env, endpoint, bucket, access, secret = _base_et_app_pg_redis_minio(tmp_path)
    remote_key_piece = None
    remote_key_artifact = None
    s3_client = None
    piece_id = None
    job_id = None
    # Contenus déterministes pour vérif octets
    contenu_piece = b"%PDF-1.4 S3 proof piece " + uuid.uuid4().hex.encode()
    contenu_artifact = b"%PDF-1.4 artifact proof " + uuid.uuid4().hex.encode()

    try:
        assert access != "minioadmin", f"identité applicative ne doit pas être admin, got {access}"
        s3_client = S3StorageClient(
            endpoint_url=endpoint,
            bucket_name=bucket,
            access_key_id=access,
            secret_access_key=secret,
            region_name="us-east-1",
            force_path_style=True,
        )
        s3_client.ensure_bucket_exists()

        from seamtech_search.comptes.comptes import creer_utilisateur

        created = creer_utilisateur(index, "admin_s3", "Nom admin_s3", "Secret123!", "administrateur")
        client = TestClient(app)
        resp = client.post("/auth/connexion", json={"identifiant": "admin_s3", "mot_de_passe": "Secret123!"}, headers={"X-SEAMTECH-TOKEN": JETON_SERVICE})
        assert resp.status_code == 200, resp.text
        sid = resp.json().get("sid")
        assert sid, "sid manquant après connexion"

        # --- PIECE via S3 ---
        local_pdf = tmp_path / "s3_piece.pdf"
        local_pdf.write_bytes(contenu_piece)
        remote_key_piece = f"audit-test/{uuid.uuid4().hex}/s3_piece.pdf"
        uploaded = s3_client.upload_file(local_pdf, remote_key=remote_key_piece)
        assert uploaded == remote_key_piece, f"upload_file a retourné {uploaded}, attendu {remote_key_piece}"
        assert s3_client.object_exists(remote_key_piece) is True, "objet S3 pièce non trouvé après upload"
        # petit délai pour MinIO (évite 502 immédiat)
        import time as _t
        _t.sleep(0.5)
        # Supprime le fichier local pour forcer le chemin S3 (sinon le serveur sert le local)
        local_pdf.unlink()

        with index.connect() as conn:
            with conn.cursor() as cur:
                path_key = f"s3://{bucket}/{remote_key_piece}"
                cur.execute(
                    "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, is_dir, object_key, object_bucket) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s, FALSE, %s, %s) RETURNING id",
                    (path_key, str(local_pdf), "s3_piece.pdf", str(tmp_path), ".pdf", len(contenu_piece), time.time(), remote_key_piece, bucket),
                )
                piece_id = int(cur.fetchone()[0])

        resp_dl = client.get(f"/pieces/{piece_id}/telecharger", headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid})
        if resp_dl.status_code not in (200, 302):
            # diagnostic : objet S3 existe-t-il ? index contient-il la ligne ? bucket ?
            try:
                exists = s3_client.object_exists(remote_key_piece) if s3_client else "no-client"
            except Exception as e:
                exists = f"check-failed:{e}"
            pytest.fail(f"PIECE S3 status {resp_dl.status_code}: {resp_dl.text[:800]} | exists={exists} bucket={bucket} key={remote_key_piece} sid={sid[:8]}...")
        # Si 200, vérifier les octets (S3 download) ; si 302, vérifier que l'URL est présignée et pointe vers le bon bucket/key
        if resp_dl.status_code == 200:
            assert resp_dl.content == contenu_piece, f"octets PIECE via S3 diffèrent: got {len(resp_dl.content)} vs {len(contenu_piece)}"
            assert resp_dl.headers.get("X-Content-Type-Options") == "nosniff"
        else:
            loc = resp_dl.headers.get("location", "") or resp_dl.headers.get("Location", "")
            assert remote_key_piece in loc or bucket in loc, f"302 location ne contient pas le S3 key/bucket: {loc}"

        # Vérif audit PIECE_DOWNLOAD complet
        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT actor_user_id, actor_username, actor_role, action, outcome, target_type, target_id, details_json, ip_address FROM audit_logs WHERE action='PIECE_DOWNLOAD' AND target_id=%s ORDER BY id DESC LIMIT 1",
                    (str(piece_id),),
                )
                row = cur.fetchone()
                assert row is not None, "PIECE_DOWNLOAD S3 non journalisé"
                assert row[0] == created["id_utilisateur"], f"actor_user_id PIECE: {row}"
                assert row[1] == "admin_s3" and row[2] == "administrateur", f"actor PIECE: {row}"
                assert row[3] == "PIECE_DOWNLOAD" and row[4] == "success", f"outcome PIECE: {row}"
                assert row[5] == "piece" and row[6] == str(piece_id), f"target PIECE: {row}"
                # ip_address doit être présent (TestClient => testclient) : row[8] est ip_address, row[7] details_json
                assert row[8] is not None
                assert row[7] is not None

        # --- ARTIFACT via S3 (succès) ---
        artifact_pdf = tmp_path / "artifact_report.pdf"
        artifact_pdf.write_bytes(contenu_artifact)
        remote_key_artifact = f"audit-test/{uuid.uuid4().hex}/artifact_report.pdf"
        s3_client.upload_file(artifact_pdf, remote_key=remote_key_artifact)
        assert s3_client.object_exists(remote_key_artifact) is True
        _t.sleep(0.5)
        artifact_pdf.unlink()

        from seamtech_search.jobs import create_job, update_job

        job_id = uuid.uuid4().hex
        create_job(index, job_id, str(tmp_path), status="done", stage="done")
        fake_local_path = str(tmp_path / f"missing_{job_id}.pdf")
        result_payload = {
            "report_path": fake_local_path,
            "report_docx_path": None,
            "artifacts": [{"name": "technical-report.pdf", "key": remote_key_artifact}],
        }
        update_job(index, job_id, status="done", stage="done", result=result_payload)

        resp_art = client.get(f"/imports/{job_id}/artifacts/report_pdf", headers={"X-SEAMTECH-TOKEN": JETON_SERVICE, "X-SEAMTECH-SESSION-SID": sid})
        if resp_art.status_code not in (200, 302):
            try:
                exists = s3_client.object_exists(remote_key_artifact) if s3_client else "no-client"
            except Exception as e:
                exists = f"check-failed:{e}"
            pytest.fail(f"ARTIFACT S3 status {resp_art.status_code}: {resp_art.text[:800]} | exists={exists} bucket={bucket} key={remote_key_artifact}")
        if resp_art.status_code == 200:
            assert resp_art.content == contenu_artifact, "octets ARTIFACT via S3 diffèrent"
        else:
            loc = resp_art.headers.get("location", "") or resp_art.headers.get("Location", "")
            assert remote_key_artifact in loc or bucket in loc, f"302 ARTIFACT location sans key: {loc}"

        with index.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT actor_user_id, actor_username, actor_role, action, outcome, target_type, target_id, details_json FROM audit_logs WHERE action='ARTIFACT_DOWNLOAD' ORDER BY id DESC LIMIT 1"
                )
                row = cur.fetchone()
                assert row is not None, "ARTIFACT_DOWNLOAD S3 non journalisé"
                assert row[0] == created["id_utilisateur"], f"actor ARTIFACT: {row}"
                assert row[1] == "admin_s3" and row[2] == "administrateur", f"actor ARTIFACT: {row}"
                assert row[3] == "ARTIFACT_DOWNLOAD" and row[4] == "success", f"outcome ARTIFACT: {row}"
                assert row[5] == "import_artifact", f"target_type ARTIFACT: {row}"
                # target_id contient job_id et artifact
                assert job_id in str(row[6]) or remote_key_artifact in str(row[6]), f"target_id ARTIFACT: {row}"

        # Preuve S3 réelle : objets existent, pas de mock
        assert s3_client.object_exists(remote_key_piece) is True
        assert s3_client.object_exists(remote_key_artifact) is True
        _t.sleep(0.5)

    finally:
        if s3_client is not None:
            for key in (remote_key_piece, remote_key_artifact):
                if key:
                    try:
                        try:
                            s3_client.delete_file(key)
                        except Exception:
                            s3_client._get_client().delete_object(Bucket=bucket, Key=key)
                    except Exception as cleanup_exc:
                        print(f"[cleanup S3] delete {key} failed: {cleanup_exc}")
        _restore_env(orig_env)
        # Nettoie les lignes de test (pas de DROP DATABASE — on réutilise la base existante)
        # On supprime les documents et jobs créés pour ne pas polluer la suite
        try:
            with index.connect() as conn:
                with conn.cursor() as cur:
                    if piece_id is not None:
                        cur.execute("DELETE FROM documents WHERE id = %s", (piece_id,))
                    if job_id is not None:
                        cur.execute("DELETE FROM jobs WHERE job_id = %s", (job_id,))
                    # Ne pas supprimer audit_logs (historique utile), mais on peut nettoyer ceux de ce test si besoin
                conn.commit()
        except Exception as e:
            print(f"[cleanup PG] delete test rows failed: {e}")
        try:
            index.close()
        except Exception:
            pass
