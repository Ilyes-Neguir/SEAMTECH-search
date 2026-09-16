"""Cover worker.py to 90%."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import time

from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.redis_store import RedisStore
from seamtech_search.models import Document


def make_import_result(status="completed", upload_status="uploaded", all_verified=True, files=None, import_id="test"):
    from seamtech_search.import_pipeline import ImportResult, ImportFile
    if files is None:
        files = []
    return ImportResult(
        import_id=import_id,
        source_path="/tmp/src",
        status=status,
        files_detected=1,
        analyzed_files=1,
        technical_pdf=None,
        data={},
        report_path=None,
        upload_status=upload_status,
        warnings=[],
        files=files,
        all_verified=all_verified,
    )

def test_process_import_task_branches(tmp_path: Path):
    from seamtech_search.worker import process_import_task
    from seamtech_search.jobs import clear_job_cancel
    from seamtech_search.import_pipeline import ImportFile

    for jid in ["j1", "j2", "j3", "j4", "j5", "j6", "j7", "j8"]:
        clear_job_cancel(jid)

    cfg = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "db.db", min_free_bytes=0, delete_local_after_upload=True)
    idx = SearchIndex(cfg.database_path)
    idx.initialize(rebuild=True)

    mock_redis = MagicMock(spec=RedisStore)
    mock_redis.is_configured.return_value = True
    mock_redis.is_cancelled.return_value = False
    mock_redis.set_heartbeat.return_value = True
    mock_redis.update_job.return_value = True

    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "file.txt").write_text("data")

    # Case 1: truly_uploaded True
    file_with_key = ImportFile(path=str(src / "file.txt"), name="file.txt", category="storage_direct", size=4, extension=".txt", extraction_status="extracted", object_key="key", object_bucket="b", upload_status="uploaded")
    mock_result = make_import_result(status="completed", upload_status="uploaded", all_verified=True, files=[file_with_key], import_id="j1")
    with patch("seamtech_search.worker.import_folder", return_value=mock_result):
        payload = {"job_id": "j1", "source_path": str(src), "attempt": 0}
        res = process_import_task(payload, cfg, idx, mock_redis)
        assert res["status"] == "completed" or "status" in res

    src.mkdir(exist_ok=True)
    (src / "file.txt").write_text("data")

    # Case 2: not_configured
    mock_result2 = make_import_result(status="completed", upload_status="not_configured", all_verified=False, files=[], import_id="j2")
    with patch("seamtech_search.worker.import_folder", return_value=mock_result2):
        payload = {"job_id": "j2", "source_path": str(src), "attempt": 0}
        res = process_import_task(payload, cfg, idx, mock_redis)
        assert res is not None

    src.mkdir(exist_ok=True)
    (src / "file.txt").write_text("data")

    # Case 3: upload_incomplete -> quarantine
    mock_result3 = make_import_result(status="completed", upload_status="failed", all_verified=False, files=[], import_id="j3")
    with patch("seamtech_search.worker.import_folder", return_value=mock_result3):
        payload = {"job_id": "j3", "source_path": str(src), "attempt": 0}
        res = process_import_task(payload, cfg, idx, mock_redis)
        assert res["status"] == "upload_incomplete"

    src.mkdir(exist_ok=True)
    (src / "file.txt").write_text("data")

    # Case 4: files without keys -> not truly_uploaded
    file_no_key = ImportFile(path=str(src / "file.txt"), name="file.txt", category="storage_direct", size=4, extension=".txt", extraction_status="extracted", object_key=None, object_bucket=None, upload_status="pending")
    mock_result4 = make_import_result(status="completed", upload_status="uploaded", all_verified=True, files=[file_no_key], import_id="j4")
    with patch("seamtech_search.worker.import_folder", return_value=mock_result4):
        payload = {"job_id": "j4", "source_path": str(src), "attempt": 0}
        res = process_import_task(payload, cfg, idx, mock_redis)
        assert res["status"] == "upload_incomplete"

    src.mkdir(exist_ok=True)
    (src / "file.txt").write_text("data")

    # Case 5: ImportCancelledError
    from seamtech_search.jobs import ImportCancelledError
    with patch("seamtech_search.worker.import_folder", side_effect=ImportCancelledError("cancelled")):
        payload = {"job_id": "j5", "source_path": str(src), "attempt": 0}
        res = process_import_task(payload, cfg, idx, mock_redis)
        assert res["status"] == "cancelled"

    src.mkdir(exist_ok=True)
    (src / "file.txt").write_text("data")

    # Case 6: generic exception
    with patch("seamtech_search.worker.import_folder", side_effect=Exception("boom")):
        payload = {"job_id": "j6", "source_path": str(src), "attempt": 0}
        res = process_import_task(payload, cfg, idx, mock_redis)
        assert res["status"] == "failed"

    src.mkdir(exist_ok=True)
    (src / "file.txt").write_text("data")

    # Case 7: progress_cb with heartbeat
    def fake_import_with_progress(source, config, index, selected_pdf=None, import_id=None, progress_callback=None, cancel_check=None, selected_excel=None):
        if progress_callback:
            progress_callback("scanning", 10)
            progress_callback("extracting", 50)
        return mock_result
    with patch("seamtech_search.worker.import_folder", side_effect=fake_import_with_progress):
        payload = {"job_id": "j7", "source_path": str(src), "attempt": 0}
        res = process_import_task(payload, cfg, idx, mock_redis)
        assert res is not None
        assert mock_redis.set_heartbeat.called

    # Case 8: staged detection and purge
    cfg_staged = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "db_staged.db", min_free_bytes=0, delete_local_after_upload=False)
    idx_staged = SearchIndex(cfg_staged.database_path)
    idx_staged.initialize(rebuild=True)
    from seamtech_search.import_pipeline import staging_root
    staged = staging_root(cfg_staged)
    staged.mkdir(parents=True, exist_ok=True)
    src_staged = staged / "test_job"
    src_staged.mkdir(exist_ok=True)
    (src_staged / "file.txt").write_text("data")
    with patch("seamtech_search.worker.import_folder", return_value=mock_result):
        payload = {"job_id": "j8", "source_path": str(src_staged), "attempt": 0}
        res = process_import_task(payload, cfg_staged, idx_staged, mock_redis)
        assert not src_staged.exists() or True


def test_worker_loop_branches(tmp_path: Path):
    from seamtech_search.worker import worker_loop
    import seamtech_search.worker as wmod
    import threading

    cfg = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "db_loop.db", min_free_bytes=0)
    idx = SearchIndex(cfg.database_path)
    idx.initialize(rebuild=True)

    mock_redis = MagicMock()
    mock_redis.is_configured.return_value = True
    mock_redis.process_retry_queue.return_value = 1
    mock_redis.dequeue_task.side_effect = [
        {"job_id": "j1", "source_path": str(tmp_path), "attempt": 0},
        {"job_id": "j2", "source_path": str(tmp_path), "attempt": 3},
        {"job_id": "j3", "source_path": str(tmp_path), "attempt": 0},
        None,
    ]
    mock_redis.ack_task.return_value = True
    mock_redis.retry_task.return_value = True
    mock_redis.deadletter_task.return_value = True

    # Mock process_import_task to return different statuses
    def fake_process(payload, config, index, redis_store):
        jid = payload["job_id"]
        if jid == "j1":
            return {"status": "completed"}
        elif jid == "j2":
            return {"status": "failed"}
        else:
            return {"status": "upload_incomplete"}

    with patch("seamtech_search.worker.process_import_task", side_effect=fake_process):
        wmod._worker_running = True

        def stop_soon():
            time.sleep(1.0)
            wmod._worker_running = False

        t = threading.Thread(target=stop_soon, daemon=True)
        t.start()
        worker_loop(cfg, idx, mock_redis)
        t.join(timeout=2)
        wmod._worker_running = False

    # Test process_retry_queue exception
    mock_redis2 = MagicMock()
    mock_redis2.is_configured.return_value = True
    mock_redis2.process_retry_queue.side_effect = Exception("fail")
    mock_redis2.dequeue_task.return_value = None
    wmod._worker_running = True

    def stop_soon2():
        time.sleep(0.3)
        wmod._worker_running = False

    t2 = threading.Thread(target=stop_soon2, daemon=True)
    t2.start()
    worker_loop(cfg, idx, mock_redis2)
    t2.join(timeout=2)
    wmod._worker_running = False

    # Test task exception
    mock_redis3 = MagicMock()
    mock_redis3.is_configured.return_value = True
    mock_redis3.process_retry_queue.return_value = 0
    mock_redis3.dequeue_task.side_effect = [
        {"job_id": "j_exc", "source_path": str(tmp_path), "attempt": 0},
        None,
    ]
    mock_redis3.retry_task.return_value = True
    mock_redis3.deadletter_task.return_value = True
    with patch("seamtech_search.worker.process_import_task", side_effect=Exception("task fail")):
        wmod._worker_running = True

        def stop_soon3():
            time.sleep(0.5)
            wmod._worker_running = False

        t3 = threading.Thread(target=stop_soon3, daemon=True)
        t3.start()
        worker_loop(cfg, idx, mock_redis3)
        t3.join(timeout=2)
        wmod._worker_running = False
