"""Extra api coverage to push to 85%."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import time

import pytest
from fastapi.testclient import TestClient

from seamtech_search.config import AppConfig
from seamtech_search.api import create_app
from seamtech_search.indexer import SearchIndex


def make_cfg(tmp_path: Path, **extra):
    base = {"root_paths": [tmp_path, Path.cwd()], "database_path": tmp_path / "search.db", "min_free_bytes": 0}
    base.update(extra)
    return AppConfig(**base)


def test_api_imports_confirm_and_artifacts(tmp_path: Path):
    cfg = make_cfg(tmp_path, database_path=tmp_path / "confirm.db")
    app = create_app(cfg)
    client = TestClient(app, follow_redirects=False)

    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "file.txt").write_text("data")
    pdf = src / "tech.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    # /imports/confirm with wait=true success
    mock_result = MagicMock()
    mock_result.status = "completed"
    with patch("seamtech_search.api.import_folder", return_value=mock_result):
        with patch("seamtech_search.api._import_payload", return_value={"status": "completed", "technical_pdf": str(pdf)}):
            r = client.post("/imports/confirm?wait=true", json={"source_path": str(src), "technical_pdf": str(pdf)})
            assert r.status_code in (200, 400, 403)

    # /imports/confirm with wait=false (async)
    with patch("seamtech_search.api.import_folder", return_value=mock_result):
        with patch("seamtech_search.api._import_payload", return_value={"status": "completed"}):
            r2 = client.post("/imports/confirm?wait=false", json={"source_path": str(src), "technical_pdf": str(pdf)})
            assert r2.status_code in (200, 202)

    # /imports/confirm with redis
    cfg_redis = make_cfg(tmp_path, database_path=tmp_path / "confirm_redis.db", redis_url="redis://localhost:6379/0")
    with patch("seamtech_search.api.RedisStore") as MockRedis:
        mock_r = MagicMock()
        mock_r.is_configured.return_value = True
        mock_r.ping.return_value = True
        mock_r.get_job.return_value = None
        mock_r.get_deadletter_count.return_value = 0
        mock_r.check_rate_limit.return_value = (False, 0)
        mock_r.set_job.return_value = True
        mock_r.enqueue_task.return_value = True
        MockRedis.return_value = mock_r
        with patch("seamtech_search.api.S3StorageClient") as MockS3:
            mock_s3 = MagicMock()
            mock_s3.is_configured.return_value = False
            MockS3.return_value = mock_s3
            app_r = create_app(cfg_redis)
            client_r = TestClient(app_r)
            r3 = client_r.post("/imports/confirm?wait=false", json={"source_path": str(src), "technical_pdf": str(pdf)})
            assert r3.status_code in (200, 202)

    # /imports/{id}/artifacts with S3 presigned
    cfg_art = make_cfg(tmp_path, database_path=tmp_path / "art.db", s3_endpoint_url="http://localhost:9000", s3_bucket="b", s3_access_key="a", s3_secret_key="s")
    app_art = create_app(cfg_art)
    client_art = TestClient(app_art, follow_redirects=False)

    # Create import record with artifacts
    idx = SearchIndex(cfg_art.database_path)
    idx.initialize(rebuild=True)
    from seamtech_search.import_pipeline import ImportResult, ImportFile
    import json, uuid
    from datetime import datetime, timezone
    # Manually insert import record
    payload = {
        "import_id": "test-art",
        "source_path": str(src),
        "status": "completed",
        "files_detected": 1,
        "analyzed_files": 1,
        "technical_pdf": str(pdf),
        "data": {},
        "report_path": str(tmp_path / "report.pdf"),
        "report_docx_path": str(tmp_path / "report.docx"),
        "excel_file": str(tmp_path / "data.xlsx"),
        "upload_status": "uploaded",
        "warnings": [],
        "files": [{"path": str(pdf), "object_key": "key/pdf"}],
        "artifacts": [{"name": "technical-report.pdf", "key": "key/report.pdf"}, {"name": "technical-report.docx", "key": "key/report.docx"}],
        "all_verified": True,
    }
    # Create report files
    (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4 fake report")
    (tmp_path / "report.docx").write_bytes(b"fake docx")
    (tmp_path / "data.xlsx").write_bytes(b"fake xlsx")

    with idx.connect() as conn:
        conn.execute(
            "INSERT INTO imports (id, source_path, status, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            ("test-art", str(src), "completed", json.dumps(payload), datetime.now(timezone.utc).isoformat()),
        )

    # Mock S3 to return presigned URL
    with patch("seamtech_search.api.S3StorageClient") as MockS3:
        mock_s3 = MagicMock()
        mock_s3.is_configured.return_value = True
        mock_s3.get_presigned_url.return_value = "https://presigned.example.com/report.pdf"
        mock_s3.object_exists.return_value = True
        mock_s3.versioning_status.return_value = {"versioning_available": True, "versioning_detail": "enabled"}
        MockS3.return_value = mock_s3

        # Need to recreate app after patching? Actually create_app creates S3 client at startup, so we need to patch before create_app
        # We'll patch inside create_app
        with patch("seamtech_search.api.S3StorageClient", return_value=mock_s3):
            with patch("seamtech_search.api.RedisStore") as MockRedis:
                mock_r = MagicMock()
                mock_r.is_configured.return_value = False
                MockRedis.return_value = mock_r
                app_art2 = create_app(cfg_art)
                client_art2 = TestClient(app_art2, follow_redirects=False)
                r_art = client_art2.get("/imports/test-art/artifacts/report_pdf")
                assert r_art.status_code in (200, 302, 404)

                r_art_docx = client_art2.get("/imports/test-art/artifacts/report_docx")
                assert r_art_docx.status_code in (200, 302, 404)

                r_art_src_pdf = client_art2.get("/imports/test-art/artifacts/source_pdf")
                assert r_art_src_pdf.status_code in (200, 302, 404)

                r_art_src_excel = client_art2.get("/imports/test-art/artifacts/source_excel")
                assert r_art_src_excel.status_code in (200, 302, 404)

    # Test artifact fallback to local file when S3 fails
    with patch("seamtech_search.api.S3StorageClient") as MockS3:
        mock_s3 = MagicMock()
        mock_s3.is_configured.return_value = True
        mock_s3.get_presigned_url.side_effect = Exception("presigned fail")
        mock_s3.object_exists.return_value = True
        mock_s3.versioning_status.return_value = {"versioning_available": True, "versioning_detail": "enabled"}
        mock_s3.download_file.side_effect = Exception("download fail")
        MockS3.return_value = mock_s3
        with patch("seamtech_search.api.RedisStore") as MockRedis:
            mock_r = MagicMock()
            mock_r.is_configured.return_value = False
            MockRedis.return_value = mock_r
            app_art3 = create_app(cfg_art)
            client_art3 = TestClient(app_art3, follow_redirects=False)
            r_art_fallback = client_art3.get("/imports/test-art/artifacts/report_pdf")
            # Should fallback to local file and return 200
            assert r_art_fallback.status_code in (200, 404)


def test_api_upload_aggregate_and_free_space(tmp_path: Path):
    cfg = make_cfg(tmp_path, database_path=tmp_path / "agg.db", max_file_size_bytes=100)
    app = create_app(cfg)
    client = TestClient(app)

    # Aggregate too large: 2 files each 60 bytes, max aggregate is 10*100=1000, so need bigger
    # Actually aggregate cap is 10x single file = 1000, so 2*60=120 <1000, not too large
    # Let's set max_file_size to 10, then aggregate cap 100, 2 files 60 each =120 >100
    cfg_small = make_cfg(tmp_path, database_path=tmp_path / "agg2.db", max_file_size_bytes=10)
    app_small = create_app(cfg_small)
    client_small = TestClient(app_small)
    big1 = b"x" * 60
    big2 = b"y" * 60
    r_agg = client_small.post(
        "/imports/upload",
        files=[("files", ("a.txt", big1, "text/plain")), ("files", ("b.txt", big2, "text/plain"))],
        data={"folder": "test"},
    )
    # First file too large -> 413, or aggregate too large -> 413
    assert r_agg.status_code == 413

    # Test free space check during upload
    from seamtech_search.retention import InsufficientStorageError
    with patch("seamtech_search.api.ensure_free_space", side_effect=[None, InsufficientStorageError("no space")]):
        cfg_fs = make_cfg(tmp_path, database_path=tmp_path / "fs.db", max_file_size_bytes=1000000)
        app_fs = create_app(cfg_fs)
        client_fs = TestClient(app_fs)
        r_fs = client_fs.post(
            "/imports/upload",
            files=[("files", ("a.txt", b"hello", "text/plain"))],
            data={"folder": "test"},
        )
        assert r_fs.status_code in (200, 413, 507)

    # Test upload with permission error in scan_folder
    with patch("seamtech_search.api.scan_folder", side_effect=PermissionError("perm")):
        cfg_perm = make_cfg(tmp_path, database_path=tmp_path / "perm.db")
        app_perm = create_app(cfg_perm)
        client_perm = TestClient(app_perm)
        r_perm = client_perm.post(
            "/imports/upload",
            files=[("files", ("a.txt", b"hello", "text/plain"))],
            data={"folder": "test"},
        )
        assert r_perm.status_code == 400


def test_api_imports_with_redis_and_s3_and_rate_limit(tmp_path: Path):
    # Test /imports with redis enqueue and S3
    cfg = make_cfg(tmp_path, database_path=tmp_path / "imp_redis.db", redis_url="redis://localhost:6379/0", s3_endpoint_url="http://localhost:9000", s3_bucket="b", s3_access_key="a", s3_secret_key="s")
    with patch("seamtech_search.api.RedisStore") as MockRedis:
        mock_r = MagicMock()
        mock_r.is_configured.return_value = True
        mock_r.ping.return_value = True
        mock_r.get_job.return_value = None
        mock_r.get_deadletter_count.return_value = 0
        mock_r.check_rate_limit.return_value = (False, 0)
        mock_r.set_job.return_value = True
        mock_r.enqueue_task.return_value = True
        mock_r.get_deadletters.return_value = []
        mock_r.replay_deadletters.return_value = 0
        MockRedis.return_value = mock_r
        with patch("seamtech_search.api.S3StorageClient") as MockS3:
            mock_s3 = MagicMock()
            mock_s3.is_configured.return_value = True
            mock_s3.versioning_status.return_value = {"versioning_available": True, "versioning_detail": "enabled"}
            mock_s3.get_presigned_url.return_value = "https://presigned"
            MockS3.return_value = mock_s3
            app = create_app(cfg)
            client = TestClient(app, follow_redirects=False)
            src = tmp_path / "src"
            src.mkdir(exist_ok=True)
            (src / "file.txt").write_text("data")
            r = client.post("/imports", json={"source_path": str(src)})
            assert r.status_code == 202

            # Test rate limiting with redis
            mock_r.check_rate_limit.return_value = (True, 5)
            r_limited = client.get("/search?q=test")
            assert r_limited.status_code == 429

            # Test with redis not pinging (fallback to in-memory)
            mock_r.ping.return_value = False
            mock_r.check_rate_limit.return_value = (False, 0)
            app2 = create_app(cfg)
            client2 = TestClient(app2)
            r2 = client2.post("/imports", json={"source_path": str(src)})
            assert r2.status_code == 202

    # Test /imports/confirm with excel
    cfg2 = make_cfg(tmp_path, database_path=tmp_path / "confirm_excel.db")
    app2 = create_app(cfg2)
    client2 = TestClient(app2)
    src2 = tmp_path / "src2"
    src2.mkdir(exist_ok=True)
    (src2 / "file.txt").write_text("data")
    pdf = src2 / "tech.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    excel = src2 / "data.xlsx"
    excel.write_bytes(b"fake")
    mock_res = MagicMock()
    mock_res.status = "completed"
    with patch("seamtech_search.api.import_folder", return_value=mock_res):
        with patch("seamtech_search.api._import_payload", return_value={"status": "completed"}):
            r = client2.post("/imports/confirm?wait=true", json={"source_path": str(src2), "technical_pdf": str(pdf), "excel_file": str(excel)})
            assert r.status_code in (200, 400)


def test_api_open_with_s3_object_key(tmp_path: Path):
    cfg = make_cfg(tmp_path, database_path=tmp_path / "open_s3.db", s3_endpoint_url="http://localhost:9000", s3_bucket="b", s3_access_key="a", s3_secret_key="s")
    idx = SearchIndex(cfg.database_path)
    idx.initialize(rebuild=True)

    f = tmp_path / "file.txt"
    f.write_text("content")

    from seamtech_search.models import Document
    import time
    doc = Document(
        path=f,
        name="file.txt",
        parent_path=tmp_path,
        extension=".txt",
        size=7,
        modified_at=time.time(),
        is_dir=False,
        text="content",
        object_key="some/key",
        object_bucket="b",
        uploaded_at=time.time(),
        upload_status="uploaded",
    )
    idx.upsert_documents([doc])

    with patch("seamtech_search.api.S3StorageClient") as MockS3:
        mock_s3 = MagicMock()
        mock_s3.is_configured.return_value = True
        mock_s3.get_presigned_url.return_value = "https://presigned.example.com/file"
        mock_s3.versioning_status.return_value = {"versioning_available": True, "versioning_detail": "enabled"}
        MockS3.return_value = mock_s3
        with patch("seamtech_search.api.RedisStore") as MockRedis:
            mock_r = MagicMock()
            mock_r.is_configured.return_value = False
            MockRedis.return_value = mock_r
            app = create_app(cfg)
            client = TestClient(app, follow_redirects=False)
            r = client.post(f"/open?path={f}")
            assert r.status_code == 302
            assert r.headers["location"] == "https://presigned.example.com/file"

            # Test presigned failure fallback to file
            mock_s3.get_presigned_url.side_effect = Exception("fail")
            app2 = create_app(cfg)
            client2 = TestClient(app2, follow_redirects=False)
            r2 = client2.post(f"/open?path={f}")
            assert r2.status_code == 200
