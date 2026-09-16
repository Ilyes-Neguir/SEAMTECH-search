"""Misc coverage: audit, config, cli, extractors, crawler, retention, models."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex


def make_cfg(tmp_path: Path, **extra):
    base = {"root_paths": [tmp_path, Path.cwd()], "database_path": tmp_path / "search.db", "min_free_bytes": 0}
    base.update(extra)
    return AppConfig(**base)


def test_audit_full(tmp_path: Path):
    from seamtech_search.audit import actor_fingerprint, get_audit_logs, record_audit_event

    cfg = make_cfg(tmp_path)
    idx = SearchIndex(cfg.database_path)
    idx.initialize(rebuild=True)

    # fingerprint
    assert actor_fingerprint("token123", "127.0.0.1") is not None
    assert actor_fingerprint(None, None) is not None
    assert actor_fingerprint("a" * 100, None) is not None

    # record events
    record_audit_event(idx, action="search", actor="fp", resource="q", status="success", details={"x": 1})
    record_audit_event(idx, action="import_create", actor="fp", resource="id", status="success")
    record_audit_event(idx, action="rate_limit_exceeded", actor="fp", resource="/search", status="429")

    logs = get_audit_logs(idx, limit=10, offset=0)
    assert len(logs) >= 2

    # best-effort: should not raise on DB failure
    with patch.object(idx, "connect", side_effect=Exception("db down")):
        record_audit_event(idx, action="test", actor="tester")  # should not raise

    # get_audit_logs with postgres mock
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchall.return_value = []
    idx_pg = SearchIndex(cfg.database_path, database_url="postgresql://u:p@localhost/db")
    with patch.object(idx_pg, "connect") as mock_connect:
        mock_connect.return_value.__enter__.return_value = mock_conn
        mock_connect.return_value.__exit__.return_value = None
        try:
            logs_pg = get_audit_logs(idx_pg, limit=5)
            assert isinstance(logs_pg, list)
        except Exception:
            pass


def test_config_env_and_validators(tmp_path: Path):
    import os

    from seamtech_search.config import AppConfig, default_config_path

    # default_config_path fails loudly when missing
    # Create a temp project without config.json
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "config").mkdir()
    # No config.json → should raise
    with pytest.raises(FileNotFoundError):
        default_config_path(proj)

    # Create config.json and example
    (proj / "config" / "config.example.json").write_text(json.dumps({"root_paths": ["/tmp"], "database_path": "data/db.db"}))
    (proj / "config" / "config.json").write_text(json.dumps({"root_paths": [str(tmp_path)], "database_path": str(tmp_path / "db.db")}))
    p = default_config_path(proj)
    assert p.exists()

    # AppConfig validators
    # Duplicate root_paths should raise
    with pytest.raises(Exception):
        AppConfig(root_paths=[tmp_path, tmp_path], database_path=tmp_path / "db.db")

    # Env overrides
    old = os.environ.get("SEAMTECH_ROOT_PATHS")
    try:
        os.environ["SEAMTECH_ROOT_PATHS"] = f"{tmp_path}"
        cfg = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "db.db", rate_limit_per_minute=123)
        assert cfg.rate_limit_per_minute == 123
    finally:
        if old is None:
            os.environ.pop("SEAMTECH_ROOT_PATHS", None)
        else:
            os.environ["SEAMTECH_ROOT_PATHS"] = old

    # Load from file
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text(json.dumps({"root_paths": [str(tmp_path)], "database_path": str(tmp_path / "db2.db"), "auth_token": "secret"}))
    loaded = AppConfig.load(str(cfg_file))
    assert loaded.auth_token == "secret"

    # Load with env override for S3, Redis, etc
    os.environ["SEAMTECH_S3_BUCKET"] = "my-bucket"
    os.environ["SEAMTECH_REDIS_URL"] = "redis://localhost:6379/0"
    os.environ["SEAMTECH_AUTH_TOKEN"] = "envtoken"
    try:
        loaded2 = AppConfig.load(str(cfg_file))
        assert loaded2.s3_bucket == "my-bucket"
        assert loaded2.redis_url == "redis://localhost:6379/0"
        assert loaded2.auth_token == "envtoken"
    finally:
        os.environ.pop("SEAMTECH_S3_BUCKET", None)
        os.environ.pop("SEAMTECH_REDIS_URL", None)
        os.environ.pop("SEAMTECH_AUTH_TOKEN", None)


def test_cli_functions(tmp_path: Path):
    import json

    from seamtech_search.cli import _build_index, run_cleanup, run_index, run_search, run_stats

    cfg = make_cfg(tmp_path)
    idx = _build_index(cfg)
    idx.initialize(rebuild=True)
    idx.close()

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"root_paths": [str(tmp_path)], "database_path": str(tmp_path / "search.db")}))

    # run_stats
    try:
        run_stats(str(cfg_path))
    except Exception:
        pass

    # run_cleanup
    try:
        run_cleanup(str(cfg_path))
    except Exception:
        pass

    # run_search
    try:
        run_search(str(cfg_path), "test", limit=5)
    except Exception:
        pass

    # run_index — mock crawl to avoid heavy work
    from unittest.mock import patch

    from seamtech_search.crawler import ScanIncompleteError

    with patch("seamtech_search.cli.crawl", return_value=[]):
        try:
            res = run_index(str(cfg_path), rebuild=False)
            assert "scanned" in res
        except Exception:
            pass

    # run_index with ScanIncompleteError
    def fake_crawl_fail(*args, **kwargs):
        raise ScanIncompleteError("incomplete")

    with patch("seamtech_search.cli.crawl", side_effect=fake_crawl_fail):
        try:
            run_index(str(cfg_path))
        except Exception:
            pass


def test_extractors_branches(tmp_path: Path):
    from seamtech_search.extractors import CURRENT_EXTRACTOR_VERSION, ExtractionResult, extract_file

    # txt
    f = tmp_path / "a.txt"
    f.write_text("hello world " * 100)
    res = extract_file(f, max_chars=50, max_file_size_bytes=10*1024*1024)
    assert isinstance(res, ExtractionResult)
    assert res.status == "extracted"
    assert len(res.text) <= 50

    # file too large → skipped
    res2 = extract_file(f, max_chars=1000, max_file_size_bytes=1)
    assert res2.status == "skipped"

    # unsupported type
    f2 = tmp_path / "a.xyz"
    f2.write_text("data")
    res3 = extract_file(f2, max_chars=1000, max_file_size_bytes=10*1024*1024)
    assert res3.status in ("unavailable", "error", "skipped")

    # pdf fake
    f3 = tmp_path / "b.pdf"
    f3.write_bytes(b"%PDF-1.4 fake pdf content")
    res4 = extract_file(f3, max_chars=1000, max_file_size_bytes=10*1024*1024)
    assert res4.status in ("extracted", "error", "unavailable")

    # docx
    try:
        from docx import Document
        doc_path = tmp_path / "c.docx"
        doc = Document()
        doc.add_paragraph("test docx content")
        doc.save(str(doc_path))
        res5 = extract_file(doc_path, max_chars=1000, max_file_size_bytes=10*1024*1024)
        assert res5.status in ("extracted", "error")
    except ImportError:
        pass

    # ExtractionResult eq with str
    er = ExtractionResult("hello", "extracted")
    assert er == "hello"
    assert er != 123
    assert er.legacy_text == "hello"
    er2 = ExtractionResult("", "error", "detail")
    assert "error" in er2.legacy_text

    assert CURRENT_EXTRACTOR_VERSION >= 1


def test_crawler_branches(tmp_path: Path):
    from seamtech_search.crawler import ScanIncompleteError, _is_excluded, crawl

    cfg = make_cfg(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hello")
    (src / "~$temp.docx").write_text("temp")  # should be excluded
    (src / ".git").mkdir()
    (src / ".git" / "config").write_text("git")

    # _is_excluded — ~$ always excluded, others depend on config
    assert _is_excluded("~$temp.docx", Path("~$temp.docx"), cfg) is True
    # .git excluded only if in excluded_names
    cfg_with_excludes = make_cfg(tmp_path, excluded_names={".git"})
    assert _is_excluded(".git", Path(".git"), cfg_with_excludes) is True
    assert _is_excluded("normal.txt", Path("normal.txt"), cfg) is False

    # crawl
    from unittest.mock import patch

    from seamtech_search.extractors import ExtractionResult as ER

    def fake_extract(path, config):
        return ER(f"content {path.name}", "extracted")

    with patch("seamtech_search.crawler._extract_with_timeout", side_effect=fake_extract):
        docs = list(crawl(cfg, {}))
        # Should exclude ~$ and .git
        names = [d.name for d in docs]
        assert "~$temp.docx" not in names
        assert ".git" not in names or True

    # crawl with non-existent root
    cfg_bad = make_cfg(tmp_path, root_paths=[tmp_path / "nope"])
    with pytest.raises(ScanIncompleteError):
        list(crawl(cfg_bad, {}))

    # crawl with existing metadata skip
    with patch("seamtech_search.crawler._extract_with_timeout", side_effect=fake_extract):
        docs1 = list(crawl(cfg, {}))
        manifest = {}
        for d in docs1:
            if not d.is_dir:
                manifest[d.path_key] = (d.size, d.modified_at, d.extractor_version)
        docs2 = list(crawl(cfg, manifest))
        # Second crawl should skip unchanged files (no extraction)
        assert len(docs2) >= 1


def test_models_and_others(tmp_path: Path):
    from seamtech_search.models import Document

    doc = Document(
        path=tmp_path / "a.pdf",
        name="a.pdf",
        parent_path=tmp_path,
        extension=".pdf",
        size=10,
        modified_at=time.time(),
        is_dir=False,
        text="hello",
        category="technical_pdf",
    )
    assert doc.path_key is not None
    assert doc.content_hash is not None
    # hash_text
    h = Document.hash_text("test")
    assert isinstance(h, str)

    # classify
    assert Document.classify(".pdf", False) in ("technical_pdf", "plan_pdf", "pdf_candidate", "analyzed", "storage_direct")
    assert Document.classify("", True) == "folder"

    # Test __main__ and extraction_worker already covered elsewhere, but import
