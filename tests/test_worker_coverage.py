"""Coverage for worker.py — purge gate, quarantine, cancel, exception."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.import_pipeline import ImportFile, ImportResult, quarantine_root, staging_root
from seamtech_search.indexer import SearchIndex
from seamtech_search.worker import process_import_task, worker_loop, start_background_worker, stop_background_worker


def make_config(tmp_path: Path, extra: dict | None = None) -> AppConfig:
    base = {"root_paths": [tmp_path, Path.cwd()], "database_path": tmp_path / "search.db", "min_free_bytes": 0}
    if extra:
        base.update(extra)
    return AppConfig(**base)


def make_index(tmp_path: Path) -> SearchIndex:
    cfg = make_config(tmp_path)
    idx = SearchIndex(cfg.database_path)
    idx.initialize(rebuild=True)
    return idx


def fake_result(tmp_path: Path, upload_status: str, all_verified: bool, with_key: bool, src_path: Path) -> ImportResult:
    f = src_path / "file.txt"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("data")
    report_pdf = tmp_path / "report.pdf"
    report_pdf.write_bytes(b"pdf")
    report_docx = tmp_path / "report.docx"
    report_docx.write_bytes(b"docx")
    imp_file = ImportFile(
        path=str(f),
        name="file.txt",
        category="storage_direct",
        size=4,
        extension=".txt",
        extraction_status="success",
        report_path=str(report_pdf),
        upload_status=upload_status,
        object_key="some/key" if with_key else None,
        object_bucket="bucket" if with_key else None,
        uploaded_at=time.time() if with_key else None,
    )
    return ImportResult(
        import_id="job-123",
        source_path=str(src_path),
        status="completed",
        files_detected=1,
        analyzed_files=1,
        technical_pdf=str(f),
        data={"reference": "X"},
        report_path=str(report_pdf),
        report_docx_path=str(report_docx),
        upload_status=upload_status,
        warnings=[],
        files=[imp_file],
        candidates=[],
        all_verified=all_verified,
    )


def test_truly_uploaded_purges_staged(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.database_path.parent.mkdir(parents=True, exist_ok=True)
    staged = staging_root(cfg)
    staged.mkdir(parents=True, exist_ok=True)
    src = staged / "uuid_folder"
    src.mkdir()
    (src / "file.txt").write_text("data")

    idx = make_index(tmp_path / "idx1")
    result = fake_result(tmp_path, upload_status="uploaded", all_verified=True, with_key=True, src_path=src)
    result = replace(result, files=[replace(result.files[0], path=str(src / "file.txt"), object_key="k", object_bucket="b", uploaded_at=time.time())], source_path=str(src), technical_pdf=str(src / "file.txt"))

    def mock_import(*args, **kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            cb("scanning", 10)
            cb("extracting", 35)
        return result

    with patch("seamtech_search.worker.import_folder", side_effect=mock_import):
        with patch("seamtech_search.worker.update_job"):
            payload = {"job_id": "job-123", "source_path": str(src)}
            out = process_import_task(payload, cfg, idx, redis_store=None)
            assert out["status"] == "completed"
            assert not src.exists()


def test_not_configured_keeps_completed(tmp_path: Path) -> None:
    src = tmp_path / "src_nc"
    src.mkdir()
    (src / "file.txt").write_text("data")
    cfg = make_config(tmp_path)
    idx = make_index(tmp_path / "idx2")
    result = fake_result(tmp_path, upload_status="not_configured", all_verified=False, with_key=False, src_path=src)
    result = replace(result, files=[replace(result.files[0], path=str(src / "file.txt"), object_key=None, object_bucket=None, uploaded_at=None)], source_path=str(src))

    with patch("seamtech_search.worker.import_folder", return_value=result):
        with patch("seamtech_search.worker.update_job"):
            payload = {"job_id": "job-123", "source_path": str(src)}
            out = process_import_task(payload, cfg, idx, redis_store=None)
            assert out["status"] == "completed"
            # Not staged and delete_local False → stays
            assert src.exists()


def test_upload_incomplete_quarantines(tmp_path: Path) -> None:
    src = tmp_path / "src_fail"
    src.mkdir()
    (src / "file.txt").write_text("data")
    cfg = make_config(tmp_path)
    idx = make_index(tmp_path / "idx3")
    result = fake_result(tmp_path, upload_status="failed", all_verified=False, with_key=False, src_path=src)
    result = replace(result, files=[replace(result.files[0], path=str(src / "file.txt"), object_key=None, upload_status="failed")], source_path=str(src))

    with patch("seamtech_search.worker.import_folder", return_value=result):
        with patch("seamtech_search.worker.update_job"):
            payload = {"job_id": "job-123", "source_path": str(src)}
            out = process_import_task(payload, cfg, idx, redis_store=None)
            assert out["status"] == "upload_incomplete"
            assert out["upload_status"] == "upload_incomplete"
            q_root = quarantine_root(cfg)
            assert q_root.exists()
            assert not src.exists()
            assert len(list(q_root.iterdir())) >= 1


def test_cancelled_and_exception(tmp_path: Path) -> None:
    src = tmp_path / "src_cancel"
    src.mkdir()
    cfg = make_config(tmp_path)
    idx = make_index(tmp_path / "idx_cancel")
    from seamtech_search.import_pipeline import ImportCancelledError

    with patch("seamtech_search.worker.import_folder", side_effect=ImportCancelledError("cancel")):
        with patch("seamtech_search.worker.update_job") as mock_update:
            payload = {"job_id": "job-123", "source_path": str(src)}
            out = process_import_task(payload, cfg, idx, redis_store=None)
            assert out["status"] == "cancelled"

    with patch("seamtech_search.worker.import_folder", side_effect=Exception("boom")):
        with patch("seamtech_search.worker.update_job"):
            payload = {"job_id": "job-123", "source_path": str(src)}
            out = process_import_task(payload, cfg, idx, redis_store=None)
            assert out["status"] == "failed"
            assert "boom" in out.get("error", "")


def test_with_redis_store(tmp_path: Path) -> None:
    src = tmp_path / "src_redis"
    src.mkdir()
    (src / "f.txt").write_text("x")
    cfg = make_config(tmp_path)
    idx = make_index(tmp_path / "idx_redis")
    result = fake_result(tmp_path, upload_status="uploaded", all_verified=True, with_key=True, src_path=src)
    result = replace(result, source_path=str(src))

    mock_redis = MagicMock()
    mock_redis.is_configured.return_value = True
    mock_redis.set_heartbeat.return_value = True
    mock_redis.update_job.return_value = True

    with patch("seamtech_search.worker.import_folder", return_value=result):
        with patch("seamtech_search.worker.update_job"):
            payload = {"job_id": "job-123", "source_path": str(src)}
            out = process_import_task(payload, cfg, idx, redis_store=mock_redis)
            assert mock_redis.update_job.call_count >= 1


def test_worker_loop_ack_retry_deadletter(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    idx = make_index(tmp_path / "idx_loop")
    mock_redis = MagicMock()
    mock_redis.process_retry_queue.return_value = 0
    task1 = {"job_id": "job-1", "source_path": str(tmp_path), "attempt": 0}
    task2 = {"job_id": "job-2", "source_path": str(tmp_path), "attempt": 3}
    mock_redis.dequeue_task.side_effect = [task1, task2, None]

    result_incomplete = {"job_id": "job-1", "status": "upload_incomplete"}
    result_failed = {"job_id": "job-2", "status": "failed"}

    with patch("seamtech_search.worker.process_import_task", side_effect=[result_incomplete, result_failed]):
        import threading

        def stop_after():
            time.sleep(0.4)
            from seamtech_search import worker as w
            w._worker_running = False

        threading.Thread(target=stop_after, daemon=True).start()
        worker_loop(cfg, idx, mock_redis)

        assert mock_redis.ack_task.call_count >= 1
        # One retry, one deadletter
        assert mock_redis.retry_task.call_count >= 1 or mock_redis.deadletter_task.call_count >= 1


def test_start_stop_worker(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    idx = make_index(tmp_path / "idx_bg")
    mock_redis = MagicMock()
    mock_redis.is_configured.return_value = False
    mock_redis.ping.return_value = False
    start_background_worker(cfg, idx, mock_redis)

    mock_redis2 = MagicMock()
    mock_redis2.is_configured.return_value = True
    mock_redis2.ping.return_value = True
    with patch("seamtech_search.worker.worker_loop") as mock_loop:
        start_background_worker(cfg, idx, mock_redis2)
        time.sleep(0.1)
        stop_background_worker()
        from seamtech_search import worker as w
        assert w._worker_running is False
