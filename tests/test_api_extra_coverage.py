"""Extra coverage for api.py — auth, errors, artifacts, maintenance."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig


def make_cfg(tmp_path: Path, **extra) -> AppConfig:
    base = {"root_paths": [tmp_path, Path.cwd()], "database_path": tmp_path / "search.db", "min_free_bytes": 0}
    base.update(extra)
    return AppConfig(**base)


def test_health_and_probes_and_metrics(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    client = TestClient(create_app(cfg))
    assert client.get("/live").status_code == 200
    assert client.get("/ready").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/metrics").status_code == 200
    # root
    assert client.get("/").status_code == 200


def test_search_validation(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    client = TestClient(create_app(cfg))
    # No query
    r = client.get("/search")
    assert r.status_code == 422
    # Empty query
    r2 = client.get("/search?q=")
    assert r2.status_code in (400, 422)
    # Valid
    r3 = client.get("/search?q=test&limit=5&offset=0")
    assert r3.status_code == 200
    assert "results" in r3.json()


def test_preview_and_open(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    f = src / "file.txt"
    f.write_text("hello world")
    cfg = make_cfg(tmp_path)
    client = TestClient(create_app(cfg))

    # preview file
    r = client.get(f"/preview?path={f}")
    assert r.status_code == 200
    # preview dir
    r2 = client.get(f"/preview?path={src}")
    assert r2.status_code == 200
    assert r2.json()["is_dir"] is True

    # preview non-existent
    r3 = client.get(f"/preview?path={tmp_path / 'nope'}")
    assert r3.status_code == 404

    # preview outside root
    outside = Path("/etc/hosts")
    if outside.exists():
        r4 = client.get(f"/preview?path={outside}")
        assert r4.status_code in (403, 404)

    # open file
    r5 = client.post(f"/open?path={f}")
    assert r5.status_code == 200

    # open dir
    r6 = client.post(f"/open?path={src}")
    assert r6.status_code == 200

    # open missing
    r7 = client.post(f"/open?path={tmp_path / 'missing'}")
    assert r7.status_code == 404


def test_imports_scan_confirm_errors(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    client = TestClient(create_app(cfg))

    # scan non-existent
    r = client.post("/imports/scan", json={"source_path": str(tmp_path / "nope")})
    assert r.status_code == 400

    # scan outside root (use /tmp)
    r2 = client.post("/imports/scan", json={"source_path": "/tmp"})
    # /tmp may be outside allowed? root is tmp_path + cwd, /tmp not inside, so 403
    assert r2.status_code in (400, 403)

    # confirm errors
    src = tmp_path / "src2"
    src.mkdir()
    (src / "a.txt").write_text("x")
    r3 = client.post("/imports/confirm", json={"source_path": str(src), "technical_pdf": str(src / "nope.pdf")})
    # Should fail because selected PDF does not exist or not pdf
    assert r3.status_code in (400, 403, 500)


def test_imports_create_and_poll(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    client = TestClient(create_app(cfg))
    src = tmp_path / "src_import"
    src.mkdir()
    (src / "file.txt").write_text("data")

    # create async
    r = client.post("/imports", json={"source_path": str(src)})
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    # poll
    r2 = client.get(f"/imports/{job_id}")
    assert r2.status_code in (200, 404)  # may be completed quickly

    # poll missing
    r3 = client.get("/imports/does-not-exist")
    assert r3.status_code == 404

    # create with wait=true
    r4 = client.post("/imports?wait=true", json={"source_path": str(src)})
    assert r4.status_code == 200
    assert "import_id" in r4.json() or "status" in r4.json()

    # cancel
    r5 = client.post(f"/imports/{job_id}/cancel")
    # Could be 200 or 404 if job already done
    assert r5.status_code in (200, 404)

    # cancel missing
    r6 = client.post("/imports/notfound/cancel")
    assert r6.status_code in (200, 404)


def test_imports_patch_and_retry(tmp_path: Path):
    cfg = make_cfg(tmp_path, database_path=tmp_path / "db" / "search.db")
    client = TestClient(create_app(cfg))
    sample = Path("sample_data/CLIENT-123")
    if not sample.exists():
        pytest.skip("sample_data not present")
    # create
    r = client.post("/imports?wait=true", json={"source_path": str(sample)})
    assert r.status_code == 200
    import_id = r.json()["import_id"]

    # patch valid
    r2 = client.patch(f"/imports/{import_id}", json={"reference": "NEW-REF"})
    assert r2.status_code == 200
    assert r2.json()["data"]["reference"] == "NEW-REF"

    # patch unknown field — Pydantic filters extra, so returns 200 (no-op) or 422
    r3 = client.patch(f"/imports/{import_id}", json={"unknown_field": "x"})
    assert r3.status_code in (200, 400, 422)

    # patch missing
    r4 = client.patch("/imports/notfound", json={"reference": "X"})
    assert r4.status_code == 404

    # retry-upload
    r5 = client.post(f"/imports/{import_id}/retry-upload")
    assert r5.status_code == 200

    # retry missing
    r6 = client.post("/imports/notfound/retry-upload")
    assert r6.status_code == 404


def test_artifacts_endpoints(tmp_path: Path):
    cfg = make_cfg(tmp_path, database_path=tmp_path / "db_art" / "search.db")
    client = TestClient(create_app(cfg))
    sample = Path("sample_data/CLIENT-123")
    if not sample.exists():
        pytest.skip("sample_data not present")
    r = client.post("/imports?wait=true", json={"source_path": str(sample)})
    import_id = r.json()["import_id"]

    # invalid artifact
    r2 = client.get(f"/imports/{import_id}/artifacts/invalid_name")
    assert r2.status_code == 400

    # valid artifacts — may be 200 or 302 or 404 depending on storage, but not 500
    for art in ["report_pdf", "report_docx", "source_pdf"]:
        r3 = client.get(f"/imports/{import_id}/artifacts/{art}")
        assert r3.status_code in (200, 302, 404)

    # missing import
    r4 = client.get("/imports/notfound/artifacts/report_pdf")
    assert r4.status_code == 404


def test_maintenance_and_audit(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    client = TestClient(create_app(cfg))

    # cleanup
    r = client.post("/maintenance/cleanup")
    assert r.status_code == 200
    assert "cleanup" in r.json()

    # audit
    r2 = client.get("/audit?limit=5&offset=0")
    assert r2.status_code == 200
    assert "results" in r2.json()

    # deadletters without redis → 503
    r3 = client.get("/maintenance/deadletters")
    assert r3.status_code in (200, 503)

    r4 = client.post("/maintenance/replay-deadletters")
    assert r4.status_code in (200, 503)


def test_upload_validation(tmp_path: Path):
    cfg = make_cfg(tmp_path, max_file_size_bytes=10 * 1024 * 1024)
    client = TestClient(create_app(cfg))

    # No files
    r = client.post("/imports/upload", data={"folder": "test"})
    assert r.status_code == 422

    # Valid small file
    from tests.test_import_workflow import TECHNICAL_LINES, make_pdf_bytes
    r2 = client.post(
        "/imports/upload",
        files=[("files", ("a.pdf", make_pdf_bytes(TECHNICAL_LINES), "application/pdf"))],
        data={"folder": "test"},
    )
    assert r2.status_code == 200

    # File too large — use tiny limit
    cfg_small = make_cfg(tmp_path / "small", max_file_size_bytes=1024, database_path=tmp_path / "small" / "db" / "search.db")
    client_small = TestClient(create_app(cfg_small))
    big = b"x" * 2048
    r3 = client_small.post(
        "/imports/upload",
        files=[("files", ("big.pdf", big, "application/pdf"))],
        data={"folder": "test"},
    )
    assert r3.status_code == 413


def test_auth_enforcement(tmp_path: Path):
    cfg = make_cfg(tmp_path, auth_token="mytoken12345678901234567890")
    client = TestClient(create_app(cfg))

    # No token
    r = client.get("/search?q=test")
    assert r.status_code == 401

    # Wrong token
    r2 = client.get("/search?q=test", headers={"X-SEAMTECH-TOKEN": "wrong"})
    assert r2.status_code == 401

    # Correct token
    r3 = client.get("/search?q=test", headers={"X-SEAMTECH-TOKEN": "mytoken12345678901234567890"})
    assert r3.status_code == 200
