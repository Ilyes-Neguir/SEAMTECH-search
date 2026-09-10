import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig


def test_async_import_job_lifecycle(tmp_path: Path) -> None:
    source = Path("sample_data/CLIENT-123").resolve()
    config = AppConfig(
        root_paths=[source.parent],
        database_path=tmp_path / "search.db",
        min_free_bytes=0,
    )
    client = TestClient(create_app(config))

    # 1. Async POST /imports returns 202 Accepted with job_id
    resp = client.post("/imports", json={"source_path": str(source)})
    assert resp.status_code == 202
    data = resp.json()
    job_id = data["job_id"]
    assert data["status"] in ("pending", "running", "completed")

    # 2. Poll GET /imports/{id} until completed
    for _ in range(50):
        poll = client.get(f"/imports/{job_id}")
        assert poll.status_code == 200
        poll_data = poll.json()
        if poll_data["status"] == "completed":
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"Job {job_id} did not complete in time, last status: {poll_data}")

    assert poll_data["status"] == "completed"
    assert poll_data["progress"] == 100
    assert poll_data.get("data") is not None
    assert poll_data["data"]["reference"] == "REF-2026-CLIENT123"


def test_sync_import_with_wait_parameter(tmp_path: Path) -> None:
    source = Path("sample_data/CLIENT-123").resolve()
    config = AppConfig(
        root_paths=[source.parent],
        database_path=tmp_path / "search.db",
        min_free_bytes=0,
    )
    client = TestClient(create_app(config))

    # ?wait=true returns 200 with complete result immediately
    resp = client.post("/imports?wait=true", json={"source_path": str(source)})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["data"]["reference"] == "REF-2026-CLIENT123"


def test_cancel_import_job(tmp_path: Path) -> None:
    source = Path("sample_data/CLIENT-123").resolve()
    config = AppConfig(
        root_paths=[source.parent],
        database_path=tmp_path / "search.db",
        min_free_bytes=0,
    )
    client = TestClient(create_app(config))

    # Post async import and cancel immediately
    resp = client.post("/imports", json={"source_path": str(source)})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    cancel_resp = client.post(f"/imports/{job_id}/cancel")
    assert cancel_resp.status_code == 200
    cancel_data = cancel_resp.json()
    assert cancel_data["status"] == "cancelled"

    # Reading the cancelled job
    poll = client.get(f"/imports/{job_id}")
    assert poll.status_code == 200
    assert poll.json()["status"] == "cancelled"


def test_disk_guard_insufficient_storage_507(tmp_path: Path) -> None:
    source = Path("sample_data/CLIENT-123").resolve()
    # Require an impossible 1000 TB of free space
    config = AppConfig(
        root_paths=[source.parent],
        database_path=tmp_path / "search.db",
        min_free_bytes=10**16,
    )
    client = TestClient(create_app(config))

    resp = client.post("/imports", json={"source_path": str(source)})
    assert resp.status_code == 507
    assert "Insufficient disk space" in resp.json()["detail"]


def test_probes_liveness_and_readiness(tmp_path: Path) -> None:
    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "search.db",
        min_free_bytes=0,
    )
    client = TestClient(create_app(config))

    live = client.get("/live")
    assert live.status_code == 200
    assert live.json() == {"status": "alive"}

    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}


def test_rate_limiting_and_retry_after(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config = AppConfig(
        root_paths=[root],
        database_path=tmp_path / "search.db",
        rate_limit_per_minute=3,
        min_free_bytes=0,
    )
    client = TestClient(create_app(config))

    # 3 allowed requests
    for _ in range(3):
        res = client.get("/search?q=test")
        assert res.status_code == 200
        assert "X-Request-ID" in res.headers

    # 4th request exceeds rate limit -> 429
    limited = client.get("/search?q=test")
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers
    assert int(limited.headers["Retry-After"]) >= 1

    # Exempt endpoints still work
    assert client.get("/live").status_code == 200
    assert client.get("/ready").status_code == 200
    assert client.get("/health").status_code == 200


def test_maintenance_cleanup_and_audit_log(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config = AppConfig(
        root_paths=[root],
        database_path=tmp_path / "search.db",
        auth_token="super-secret-token",
        min_free_bytes=0,
    )
    client = TestClient(create_app(config))
    headers = {"X-SEAMTECH-TOKEN": "super-secret-token"}

    # Run cleanup
    cleanup_resp = client.post("/maintenance/cleanup", headers=headers)
    assert cleanup_resp.status_code == 200
    assert "cleanup" in cleanup_resp.json()

    # Query audit logs
    audit_resp = client.get("/audit", headers=headers)
    assert audit_resp.status_code == 200
    audit_data = audit_resp.json()
    assert audit_data["count"] >= 1

    # Check actor fingerprint: raw secret must NEVER appear
    for entry in audit_data["results"]:
        assert "super-secret-token" not in entry["actor"]
        assert entry["actor"].startswith("token:")
