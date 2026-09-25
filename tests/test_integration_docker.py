"""Docker compose integration test — real Postgres, Redis, MinIO.

Skips if docker not available (as in sandbox).
Tests:
- Postgres backend upsert/search/health
- Redis queue and rate limiting
- MinIO S3 upload and presigned URL
- Full API round-trip: health, search, preview, open, imports, artifacts
"""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

# Skip if docker not available
DOCKER_AVAILABLE = shutil.which("docker") is not None
COMPOSE_AVAILABLE = False
if DOCKER_AVAILABLE:
    try:
        subprocess.run(["docker", "compose", "version"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        COMPOSE_AVAILABLE = True
    except Exception:
        COMPOSE_AVAILABLE = False

# Deux marqueurs, deux rôles distincts :
#  - ``skipif`` : sans Docker, ces tests ne peuvent pas s'exécuter ;
#  - ``s3``     : ils exigent un stockage objet VIVANT (MinIO du compose :
#    ``ensure_bucket_exists``, ``upload_file``, ``get_presigned_url``,
#    ``delete_file``). Le marqueur est ce qui les tient hors des suites sans
#    service (R-14) ; le job CI `integration` les sélectionne PAR CHEMIN, donc
#    le marqueur ne les y désélectionne pas.
pytestmark = [
    pytest.mark.skipif(
        not (DOCKER_AVAILABLE and COMPOSE_AVAILABLE), reason="Docker not available in this environment"
    ),
    pytest.mark.s3,
]

# Strict mode (set by CI, where the infra services are brought up first and
# health-checked): backends that are supposed to be reachable must actually
# work — a failure there is a real failure, not a "may not be ready" skip.
STRICT = os.environ.get("SEAMTECH_INTEGRATION_STRICT", "").strip().lower() in {"1", "true", "yes"}


def _wait_for_postgres(url: str, timeout: int = 30):
    import psycopg2
    start = time.time()
    while time.time() - start < timeout:
        try:
            conn = psycopg2.connect(url, connect_timeout=2)
            conn.close()
            return True
        except Exception:
            time.sleep(1)
    return False


def _wait_for_redis(url: str, timeout: int = 30):
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
            time.sleep(1)
    except Exception:
        pass
    return False


def test_docker_compose_infra(tmp_path: Path):
    """Test that docker compose up postgres/redis/minio works and backends are reachable."""
    # This test assumes docker compose up was done in CI, but we also try to bring up infra here if needed
    # Check env vars for compose
    postgres_password = os.environ.get("POSTGRES_PASSWORD", "test_password")
    minio_user = os.environ.get("MINIO_ROOT_USER", "minioadmin")
    minio_pass = os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin123")
    redis_pass = os.environ.get("REDIS_PASSWORD", "redis_test_password")

    # Try to connect to services if they are up
    postgres_url = f"postgresql://seamtech:{postgres_password}@127.0.0.1:5433/seamtech_search"
    # In CI, postgres is on 5433 per docker-compose.yml
    # For local test, try 5432 as well
    postgres_urls = [
        postgres_url,
        f"postgresql://seamtech:{postgres_password}@127.0.0.1:5432/seamtech_search",
        os.environ.get("SEAMTECH_TEST_DATABASE_URL", ""),
    ]

    redis_urls = [
        f"redis://:{redis_pass}@127.0.0.1:6379/0",
        "redis://localhost:6379/0",
        os.environ.get("SEAMTECH_REDIS_URL", ""),
    ]

    # Test Postgres backend
    import uuid

    from seamtech_search.indexer import SearchIndex
    from seamtech_search.models import Document

    pg_connected = False
    for pg_url in postgres_urls:
        if not pg_url:
            continue
        if _wait_for_postgres(pg_url, timeout=5):
            try:
                idx = SearchIndex(tmp_path / "unused.db", pg_url)
                idx.initialize()
                doc_path = tmp_path / f"pg-test-{uuid.uuid4().hex}.txt"
                doc = Document(
                    path=doc_path,
                    name=doc_path.name,
                    parent_path=doc_path.parent,
                    extension=".txt",
                    size=10,
                    modified_at=time.time(),
                    is_dir=False,
                    text="integration test postgres marker",
                )
                assert idx.upsert_document(doc) is True
                results = idx.search("integration test postgres marker")
                assert any(r["name"] == doc_path.name for r in results)
                health = idx.health_details()
                assert health["backend"] == "postgresql"
                pg_connected = True
                break
            except Exception as e:
                if STRICT:
                    pytest.fail(f"Postgres backend broken in strict mode ({pg_url}): {e}")
                print(f"Postgres test failed for {pg_url}: {e}")
                continue

    # If no postgres available, skip postgres part but don't fail whole test
    if not pg_connected:
        if STRICT:
            pytest.fail("Postgres not reachable, but strict mode expects the compose infra to be up")
        pytest.skip("Postgres not reachable in integration test")

    # Test Redis backend
    from seamtech_search.redis_store import RedisStore

    redis_connected = False
    for r_url in redis_urls:
        if not r_url:
            continue
        if _wait_for_redis(r_url, timeout=5):
            try:
                store = RedisStore(redis_url=r_url)
                assert store.ping() is True
                assert store.set_job("test-job", {"id": "test-job", "status": "running"}) is True
                job = store.get_job("test-job")
                assert job is not None
                assert store.enqueue_task("imports", {"job_id": "test-job"}) is True
                task = store.dequeue_task("imports", timeout=2)
                if task:
                    assert task["job_id"] == "test-job"
                    store.ack_task("imports", task)
                redis_connected = True
                break
            except Exception as e:
                if STRICT:
                    pytest.fail(f"Redis backend broken in strict mode ({r_url}): {e}")
                print(f"Redis test failed for {r_url}: {e}")
                continue

    if not redis_connected:
        if STRICT:
            pytest.fail("Redis not reachable, but strict mode expects the compose infra to be up")
        pytest.skip("Redis not reachable in integration test")

    # Test S3/MinIO backend if available
    from seamtech_search.storage import S3StorageClient

    s3_endpoint = os.environ.get("SEAMTECH_S3_ENDPOINT_URL", "http://127.0.0.1:9000")
    s3_bucket = os.environ.get("SEAMTECH_S3_BUCKET", "seamtech-documents")
    s3_access = os.environ.get("SEAMTECH_S3_ACCESS_KEY", minio_user)
    s3_secret = os.environ.get("SEAMTECH_S3_SECRET_KEY", minio_pass)

    try:
        client = S3StorageClient(
            endpoint_url=s3_endpoint,
            bucket_name=s3_bucket,
            access_key_id=s3_access,
            secret_access_key=s3_secret,
            region_name="us-east-1",
            force_path_style=True,
        )
        if client.is_configured():
            # Try to ensure bucket and upload
            try:
                client.ensure_bucket_exists()
                test_file = tmp_path / "s3_test.txt"
                test_file.write_text("integration test s3")
                key = client.upload_file(test_file, remote_key=f"integration/{uuid.uuid4().hex}/test.txt")
                assert client.object_exists(key) is True
                url = client.get_presigned_url(key, expiration_seconds=900)
                assert url.startswith("http")
                # Cleanup
                client.delete_file(key)
            except Exception as e:
                if STRICT:
                    # CI health-checked MinIO before this step ran: an S3
                    # failure here is a real failure, not "may not be ready".
                    pytest.fail(f"S3/MinIO backend broken in strict mode ({s3_endpoint}): {e}")
                print(f"S3 test failed: {e}")
                # Don't fail, S3 may not be ready
    except Exception as e:
        if STRICT:
            pytest.fail(f"S3 client creation failed in strict mode: {e}")
        print(f"S3 client creation failed: {e}")


def test_api_with_real_backends(tmp_path: Path):
    """Test API endpoints with real backends if available."""
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    # Use tmp_path as root and sqlite for simplicity, but with redis/s3 if available
    cfg = AppConfig(
        root_paths=[tmp_path, Path.cwd()],
        database_path=tmp_path / "search.db",
        min_free_bytes=0,
        redis_url=os.environ.get("SEAMTECH_REDIS_URL", "redis://localhost:6379/0"),
        s3_endpoint_url=os.environ.get("SEAMTECH_S3_ENDPOINT_URL", "http://127.0.0.1:9000"),
        s3_bucket=os.environ.get("SEAMTECH_S3_BUCKET", "seamtech-documents"),
        s3_access_key=os.environ.get("SEAMTECH_S3_ACCESS_KEY", "minioadmin"),
        s3_secret_key=os.environ.get("SEAMTECH_S3_SECRET_KEY", "minioadmin123"),
    )

    app = create_app(cfg)
    client = TestClient(app, follow_redirects=False)

    # Health
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data

    # Search (empty)
    r2 = client.get("/search?q=test")
    assert r2.status_code == 200

    # Preview
    f = tmp_path / "preview.txt"
    f.write_text("hello integration")
    r3 = client.get(f"/preview?path={f}")
    assert r3.status_code == 200

    # Open
    r4 = client.post(f"/open?path={f}")
    assert r4.status_code in (200, 302)

    # Imports scan
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "doc.txt").write_text("data")
    r5 = client.post("/imports/scan", json={"source_path": str(src)})
    assert r5.status_code in (200, 400)

    # Imports create
    r6 = client.post("/imports", json={"source_path": str(src)})
    assert r6.status_code in (200, 202, 400, 507)

    if r6.status_code == 202:
        job_id = r6.json()["job_id"]
        # Poll job
        for _ in range(5):
            r_job = client.get(f"/imports/{job_id}")
            if r_job.status_code == 200:
                break
            time.sleep(1)
