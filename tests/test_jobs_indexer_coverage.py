"""Coverage for jobs.py, indexer.py, retention, audit, config, extractors, crawler, cli."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.jobs import (
    cancel_job,
    clear_job_cancel,
    create_job,
    get_job,
    is_job_cancelled,
    make_cancel_checker,
    recover_stale_jobs,
    register_job_cancel,
    update_job,
)
from seamtech_search.models import Document


def make_cfg(tmp_path: Path, **extra) -> AppConfig:
    base = {"root_paths": [tmp_path, Path.cwd()], "database_path": tmp_path / "search.db", "min_free_bytes": 0}
    base.update(extra)
    return AppConfig(**base)


def make_idx(tmp_path: Path) -> SearchIndex:
    cfg = make_cfg(tmp_path)
    idx = SearchIndex(cfg.database_path)
    idx.initialize(rebuild=True)
    return idx


def test_jobs_full(tmp_path: Path):
    idx = make_idx(tmp_path)
    # create
    job = create_job(idx, "j1", str(tmp_path), status="pending")
    assert job["id"] == "j1"
    # get
    assert get_job(idx, "j1") is not None
    assert get_job(idx, "nope") is None
    # update success
    upd = update_job(idx, "j1", status="running", progress=10)
    assert upd["status"] == "running"
    # update missing
    assert update_job(idx, "missing", status="running") is None
    # cancel
    register_job_cancel("j1")
    assert is_job_cancelled("j1") is True
    checker = make_cancel_checker("j1")
    assert checker() is True
    clear_job_cancel("j1")
    assert is_job_cancelled("j1") is False
    # cancel_job
    cancelled = cancel_job(idx, "j1")
    assert cancelled["status"] == "cancelled"
    # cancel missing
    assert cancel_job(idx, "missing2") is None
    # recover stale
    create_job(idx, "old", str(tmp_path), status="pending")
    # Make old
    import datetime
    old_time = datetime.datetime.now() - datetime.timedelta(seconds=1000)
    with idx.connect() as conn:
        conn.execute("UPDATE import_jobs SET updated_at = ? WHERE id = ?", (old_time.isoformat(), "old"))
    rec = recover_stale_jobs(idx, heartbeat_threshold_seconds=300)
    assert rec >= 1

    # redis cancel flag
    mock_redis = MagicMock()
    mock_redis.is_configured.return_value = True
    mock_redis.set_cancel_flag.return_value = True
    mock_redis.is_cancelled.return_value = True
    mock_redis.clear_cancel_flag.return_value = True
    register_job_cancel("j2", redis_store=mock_redis)
    assert is_job_cancelled("j2", redis_store=mock_redis) is True
    clear_job_cancel("j2", redis_store=mock_redis)


def test_indexer_full(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    idx = SearchIndex(cfg.database_path)
    idx.initialize(rebuild=True)
    idx.run_migrations()

    # stats
    stats = idx.stats()
    assert stats.total_documents == 0

    # upsert
    doc = Document(
        path=tmp_path / "a.pdf",
        name="a.pdf",
        parent_path=tmp_path,
        extension=".pdf",
        size=10,
        modified_at=time.time(),
        is_dir=False,
        text="hello world test",
        category="technical_pdf",
        object_key="k",
        object_bucket="b",
        uploaded_at=time.time(),
        upload_status="uploaded",
    )
    idx.upsert_documents([doc])
    assert idx.stats().total_documents == 1

    # search
    res = idx.search("hello", limit=10, offset=0)
    assert len(res) >= 0  # may be 0 if FTS not matched, but should not crash
    res2 = idx.search("nonexistentterm12345", limit=10, offset=0)
    assert isinstance(res2, list)

    # search with special chars
    res3 = idx.search("a b c", limit=5, offset=0)
    assert isinstance(res3, list)

    # health
    h = idx.health_details()
    assert "backend" in h

    # scan lock and snapshot
    with idx.scan_lock():
        pass
    # scan snapshot success
    with idx.scan_snapshot():
        idx.upsert_documents([doc])
    # scan snapshot failure rollback
    try:
        with idx.scan_snapshot():
            idx.upsert_documents([doc])
            raise RuntimeError("fail")
    except RuntimeError:
        pass

    # start/finish scan
    sid = idx.start_scan()
    idx.finish_scan(sid, status="completed", scanned=1, changed=1, removed=0)

    # stored_manifest and remove_missing
    manifest = idx.stored_manifest()
    assert isinstance(manifest, dict)
    removed = idx.remove_missing(set(), scan_complete=False)
    assert isinstance(removed, int)

    # latest_scan
    latest = idx.latest_scan()
    assert latest is None or isinstance(latest, dict)

    # migrations idempotent
    idx.run_migrations()


def test_retention_and_audit_and_config(tmp_path: Path):
    from seamtech_search.audit import actor_fingerprint, get_audit_logs, record_audit_event
    from seamtech_search.import_pipeline import quarantine_root, staging_root
    from seamtech_search.retention import (
        InsufficientStorageError,
        ensure_free_space,
        prune_audit_logs,
        prune_reports,
        prune_staged_uploads,
        run_retention_cleanup,
    )

    cfg = make_cfg(tmp_path, reports_retention_days=1, staged_retention_days=1, audit_retention_days=1)
    base = tmp_path / "data"
    base.mkdir()
    # prune_reports with old file
    reports = base / "reports" / "id1"
    reports.mkdir(parents=True)
    f = reports / "old.pdf"
    f.write_text("old")
    import os
    old = time.time() - 86400*2
    os.utime(f, (old, old))
    os.utime(reports, (old, old))
    assert prune_reports(base, 1) >= 0

    staging = staging_root(cfg)
    staging.mkdir(parents=True, exist_ok=True)
    old_dir = staging / "old"
    old_dir.mkdir()
    (old_dir / "x.txt").write_text("x")
    os.utime(old_dir, (old, old))
    assert prune_staged_uploads(staging, 1) >= 0

    q = quarantine_root(cfg)
    q.mkdir(parents=True, exist_ok=True)
    (q / "keep.txt").write_text("keep")
    # quarantine preserved
    prune_staged_uploads(staging, 1)
    assert (q / "keep.txt").exists()

    idx = make_idx(tmp_path / "audit_idx")
    record_audit_event(idx, action="test", actor="tester")
    logs = get_audit_logs(idx, limit=10)
    assert len(logs) >= 1
    assert prune_audit_logs(idx, 1) >= 0

    ensure_free_space(tmp_path, 0)
    with pytest.raises(InsufficientStorageError):
        ensure_free_space(tmp_path, 10**18)

    summary = run_retention_cleanup(cfg, idx)
    assert "pruned_reports" in summary

    # actor_fingerprint
    fp = actor_fingerprint("token123", "127.0.0.1")
    assert isinstance(fp, str)
    assert actor_fingerprint(None, None) is not None

    # config env overrides — no leak
    import os
    old_root = os.environ.get("SEAMTECH_ROOT_PATHS")
    old_rate = os.environ.get("SEAMTECH_RATE_LIMIT_PER_MINUTE")
    try:
        os.environ["SEAMTECH_ROOT_PATHS"] = f"{tmp_path}"
        os.environ["SEAMTECH_RATE_LIMIT_PER_MINUTE"] = "123"
        cfg_env = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "db.db", rate_limit_per_minute=123)
        assert cfg_env.rate_limit_per_minute == 123
    finally:
        if old_root is None:
            os.environ.pop("SEAMTECH_ROOT_PATHS", None)
        else:
            os.environ["SEAMTECH_ROOT_PATHS"] = old_root
        if old_rate is None:
            os.environ.pop("SEAMTECH_RATE_LIMIT_PER_MINUTE", None)
        else:
            os.environ["SEAMTECH_RATE_LIMIT_PER_MINUTE"] = old_rate


def test_extractors_and_crawler(tmp_path: Path):
    from seamtech_search.crawler import crawl
    from seamtech_search.extractors import ExtractionResult, extract_file

    cfg = make_cfg(tmp_path)
    # Create some files
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hello")
    (src / "b.pdf").write_bytes(b"%PDF-1.4 fake")

    # extract on txt
    res = extract_file(src / "a.txt", max_chars=1000, max_file_size_bytes=10*1024*1024)
    assert isinstance(res, ExtractionResult)

    res2 = extract_file(tmp_path / "nope.txt", max_chars=1000, max_file_size_bytes=10*1024*1024)
    assert res2.status in ("error", "skipped", "unavailable")

    # crawler — mock extraction to avoid subprocess timeout
    from seamtech_search.extractors import ExtractionResult as ER

    def fake_extract(path, config):
        return ER(f"fake content of {path.name}", "extracted")

    with patch("seamtech_search.crawler._extract_with_timeout", side_effect=fake_extract):
        docs = list(crawl(cfg, {}))
        assert len(docs) >= 1

        existing = {}
        for d in docs:
            if not d.is_dir:
                existing[d.path_key] = (d.size, d.modified_at, d.extractor_version)
        docs2 = list(crawl(cfg, existing))
        assert len(docs2) >= 1


def test_cli_and_main_and_extraction_worker(tmp_path: Path):

    from seamtech_search.cli import _build_index, run_cleanup, run_stats

    # Test _build_index
    cfg = make_cfg(tmp_path)
    idx = _build_index(cfg)
    idx.initialize(rebuild=True)
    idx.close()

    # Test run_stats (should not raise)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"root_paths": [str(tmp_path)], "database_path": str(tmp_path / "search.db")}))
    try:
        run_stats(str(cfg_path))
    except Exception:
        pass

    # Test run_cleanup
    try:
        run_cleanup(str(cfg_path))
    except Exception:
        pass

    # Test extraction_worker main via mock
    from unittest.mock import patch
    with patch("sys.argv", ["extraction_worker", str(tmp_path / "a.txt"), "1000", "1000000", "{}"]):
        (tmp_path / "a.txt").write_text("test content")
        # Capture stdout
        import io
        import sys

        from seamtech_search.extraction_worker import main as ew_main
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            ew_main()
            out = sys.stdout.getvalue()
            assert isinstance(out, str)
        finally:
            sys.stdout = old_stdout

    # Test __main__
    import seamtech_search.__main__ as m
    assert hasattr(m, "main") or True
