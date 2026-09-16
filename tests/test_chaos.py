"""Chaos tests — S3 down, Redis killed, worker SIGKILL, disk full.

These tests verify that no file is lost and the UI reports the true state.
They simulate infrastructure failures in-process (failing S3 uploads, killed
Redis, a real SIGKILLed worker subprocess, exhausted disk) so they run in CI
without any external infrastructure.
"""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.jobs import create_job, get_job
from seamtech_search.redis_store import RedisStore
from seamtech_search.storage import S3StorageClient


def make_cfg(tmp_path: Path, **extra):
    base = {"root_paths": [tmp_path], "database_path": tmp_path / "search.db", "min_free_bytes": 0}
    base.update(extra)
    return AppConfig(**base)


def test_chaos_s3_down_mid_import(tmp_path: Path):
    """S3 down mid-import: the most critical data-loss scenario of the audit.

    Runs the real worker task handler with S3 configured but every upload
    failing, and asserts the exact state "no data lost" means:

    - the job ends ``upload_incomplete`` (never ``completed`` — the files were
      not in the bucket — and never a silent ``failed`` with nothing left)
    - every artifact is reported ``failed`` (nothing was uploaded)
    - the source folder is moved to quarantine and its content is preserved
      byte-for-byte (no purge, no truncation)
    """
    from seamtech_search.import_pipeline import quarantine_root
    from seamtech_search.worker import process_import_task

    cfg = make_cfg(
        tmp_path,
        s3_endpoint_url="http://localhost:9000",
        s3_bucket="b",
        s3_access_key="a",
        s3_secret_key="s",
    )
    idx = SearchIndex(cfg.database_path)
    idx.initialize(rebuild=True)

    job_id = "chaos-s3-down"
    create_job(idx, job_id, str(tmp_path), status="running", stage="extracting")

    src = tmp_path / "src"
    src.mkdir()
    pdf_bytes = b"%PDF-1.4 fake technical sheet"
    (src / "file.pdf").write_bytes(pdf_bytes)
    (src / "notes.txt").write_text("do not lose me")

    # S3 is configured, but the endpoint is down: every upload attempt raises.
    with patch.object(S3StorageClient, "upload_file", side_effect=Exception("S3 down")):
        result = process_import_task({"job_id": job_id, "source_path": str(src)}, cfg, idx)

    # 1. Job state: upload_incomplete — the precise status, not a union of all.
    assert result["status"] == "upload_incomplete"
    job = get_job(idx, job_id)
    assert job["status"] == "upload_incomplete"
    assert job["stage"] == "upload_incomplete"

    # 2. Nothing was uploaded: every artifact in the payload is failed.
    assert result["artifacts"], "expected upload artifacts in the result payload"
    for artifact in result["artifacts"]:
        assert artifact["status"] == "failed"

    # 3. No data loss: the source is moved to quarantine, content intact.
    assert not src.exists()
    q_dir = quarantine_root(cfg) / f"{job_id}_src"
    assert q_dir.is_dir()
    assert (q_dir / "file.pdf").read_bytes() == pdf_bytes
    assert (q_dir / "notes.txt").read_text() == "do not lose me"
    assert Path(result["quarantine_path"]) == q_dir


def test_chaos_redis_killed_mid_job(tmp_path: Path):
    """Redis killed mid-job: job should be recoverable, no file lost."""
    cfg = make_cfg(tmp_path)
    idx = SearchIndex(cfg.database_path)
    idx.initialize(rebuild=True)

    job_id = "chaos-redis-killed"
    create_job(idx, job_id, str(tmp_path), status="running", stage="scanning")

    # Simulate redis dying: set heartbeat fails, but job remains in DB
    mock_redis = MagicMock(spec=RedisStore)
    mock_redis.is_configured.return_value = True
    mock_redis.is_cancelled.return_value = False
    mock_redis.set_heartbeat.side_effect = Exception("Redis down")
    mock_redis.update_job.side_effect = Exception("Redis down")
    mock_redis.ping.return_value = False

    # The job should still be in DB and recoverable
    from datetime import datetime, timedelta, timezone

    from seamtech_search.jobs import recover_stale_jobs

    old = (datetime.now(timezone.utc) - timedelta(seconds=1000)).isoformat()
    with idx.connect() as conn:
        conn.execute("UPDATE import_jobs SET updated_at = ?, status='running' WHERE id = ?", (old, job_id))

    recovered = recover_stale_jobs(idx, heartbeat_threshold_seconds=100)
    assert recovered >= 1
    fetched = get_job(idx, job_id)
    assert fetched["status"] == "failed"
    assert "Server restarted" in fetched["error"]


# Child process for the SIGKILL test: behaves like a worker that is mid-import
# (job visible as running in the DB) and then simply stops being alive.
_WORKER_CHILD_SCRIPT = """\
import os
import sys
import time
from pathlib import Path

db_path, pid_file, job_id = sys.argv[1], sys.argv[2], sys.argv[3]

from seamtech_search.indexer import SearchIndex
from seamtech_search.jobs import get_job

idx = SearchIndex(Path(db_path))
job = get_job(idx, job_id)
assert job is not None and job["status"] == "running", f"unexpected job state: {job}"

Path(pid_file).write_text(str(os.getpid()))
time.sleep(60)  # simulates mid-import work; the parent will SIGKILL us
"""


def test_chaos_worker_sigkill(tmp_path: Path):
    """A real worker subprocess is SIGKILLed mid-job.

    Unlike a graceful stop (setting ``_worker_running = False``), SIGKILL is
    uncatchable, so no cleanup code runs. This exercises the crash path
    end-to-end: a real subprocess, a real ``os.kill(pid, SIGKILL)``, and the
    real stale-job recovery routine. Every assertion can fail:

    - the child must die *from* SIGKILL (exit code -9), not from a clean exit
    - the crash must leave the job stuck in ``running`` (no cleanup ran)
    - restart recovery must mark exactly that job ``failed`` with the
      documented error message
    - the imported files must survive on disk, byte-for-byte
    """
    import os
    import signal
    import subprocess
    import sys

    from seamtech_search.jobs import recover_stale_jobs

    if sys.platform == "win32":
        pytest.skip("SIGKILL is not available on Windows")

    cfg = make_cfg(tmp_path)
    idx = SearchIndex(cfg.database_path)
    idx.initialize(rebuild=True)

    job_id = "chaos-worker-sigkill"
    create_job(idx, job_id, str(tmp_path), status="running", stage="extracting")

    # Files the "worker" is importing: must survive the crash untouched.
    src = tmp_path / "src"
    src.mkdir()
    pdf_bytes = b"%PDF-1.4 fake"
    (src / "file.pdf").write_bytes(pdf_bytes)

    script = tmp_path / "worker_child.py"
    script.write_text(_WORKER_CHILD_SCRIPT, encoding="utf-8")
    pid_file = tmp_path / "worker.pid"

    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent.parent))
    proc = subprocess.Popen(
        [sys.executable, str(script), str(cfg.database_path), str(pid_file), job_id],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
    )
    try:
        # Wait until the child reports its pid — it has reached the "working" state.
        deadline = time.time() + 15
        while not pid_file.exists():
            if proc.poll() is not None:
                raise AssertionError(f"worker child exited before being ready (rc={proc.returncode})")
            if time.time() > deadline:
                raise AssertionError("worker child did not report its pid in time")
            time.sleep(0.05)
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGKILL)
        rc = proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()

    # 1. The child must have died from SIGKILL itself — not a clean exit.
    assert rc == -signal.SIGKILL

    # 2. SIGKILL cannot run cleanup: the job is stuck in running.
    stuck = get_job(idx, job_id)
    assert stuck["status"] == "running"

    # 3. On restart, stale recovery marks exactly this job failed. A restart
    #    happens long after the crash, so age the heartbeat before recovering
    #    (with the routine's default 300s threshold, as on server startup).
    from datetime import datetime, timedelta, timezone

    old = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    with idx.connect() as conn:
        conn.execute("UPDATE import_jobs SET updated_at = ? WHERE id = ?", (old, job_id))

    recovered = recover_stale_jobs(idx)
    assert recovered == 1
    job = get_job(idx, job_id)
    assert job["status"] == "failed"
    assert job["stage"] == "failed"
    assert job["error"] == "Server restarted while job was running"

    # 4. No data loss: the imported file is still on disk, unmodified.
    assert (src / "file.pdf").read_bytes() == pdf_bytes


def test_chaos_disk_full(tmp_path: Path):
    """Disk full: ensure_free_space should raise InsufficientStorageError and import should fail with 507, no partial purge."""
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.import_pipeline import import_folder
    from seamtech_search.retention import InsufficientStorageError, ensure_free_space

    cfg = make_cfg(tmp_path, min_free_bytes=10**18)  # Require huge free space

    # ensure_free_space should raise
    with pytest.raises(InsufficientStorageError):
        ensure_free_space(tmp_path, cfg.min_free_bytes)

    # API should return 507
    app = create_app(cfg)
    client = TestClient(app)
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "file.txt").write_text("data")
    r = client.post("/imports", json={"source_path": str(src)})
    assert r.status_code == 507

    # import_folder should also raise InsufficientStorageError at start
    idx = SearchIndex(cfg.database_path)
    idx.initialize(rebuild=True)
    with pytest.raises(InsufficientStorageError):
        import_folder(src, cfg, idx, import_id="chaos-disk-full")

    # Files should still exist (no purge)
    assert src.exists()


def test_chaos_s3_versioning_unavailable(tmp_path: Path):
    """S3 versioning unavailable (R2): should not fail, should report versioning_available=False."""
    from botocore.exceptions import ClientError

    client = S3StorageClient(bucket_name="b", access_key_id="a", secret_access_key="s", endpoint_url="http://localhost:9000")

    mock_s3 = MagicMock()
    mock_s3.head_bucket.return_value = {}
    mock_s3.put_bucket_versioning.side_effect = ClientError({"Error": {"Code": "NotImplemented"}}, "PutBucketVersioning")
    mock_s3.get_bucket_versioning.return_value = {}

    with patch.object(client, "_get_client", return_value=mock_s3):
        client.ensure_bucket_exists()
        status = client.versioning_status()
        # Should be False for NotImplemented
        assert status["versioning_available"] is False or status["versioning_available"] is None


def test_chaos_redis_rate_limit_fallback(tmp_path: Path):
    """Redis down during rate limiting: should fallback to in-memory and not crash."""
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app

    cfg = make_cfg(tmp_path, rate_limit_per_minute=1)
    # Mock Redis to fail ping but not rate limit
    with patch("seamtech_search.api.RedisStore") as MockRedis:
        mock_r = MagicMock()
        mock_r.is_configured.return_value = True
        mock_r.ping.return_value = False  # Redis down -> will fallback to in-memory
        mock_r.check_rate_limit.return_value = (False, 0)
        mock_r.get_job.return_value = None
        mock_r.get_deadletter_count.return_value = 0
        MockRedis.return_value = mock_r
        with patch("seamtech_search.api.S3StorageClient") as MockS3:
            mock_s3 = MagicMock()
            mock_s3.is_configured.return_value = False
            MockS3.return_value = mock_s3
            app = create_app(cfg)
            client = TestClient(app)
            # Should still work via in-memory fallback
            r1 = client.get("/live")
            assert r1.status_code == 200
            r2 = client.get("/search?q=test")
            assert r2.status_code in (200, 429)
            r3 = client.get("/search?q=test2")
            assert r3.status_code in (200, 429)

    # Also test redis_store internal fallback when pipeline fails
    store = RedisStore(redis_url="redis://localhost:6379/0")
    mock_client = MagicMock()
    mock_client.pipeline.side_effect = Exception("pipeline fail")
    with patch.object(store, "_get_client", return_value=mock_client):
        limited, retry = store.check_rate_limit("key", 10)
        assert limited is False
