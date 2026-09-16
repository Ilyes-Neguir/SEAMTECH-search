"""Redis background worker for asynchronous reference imports and S3 archival.

Handles decoupled task execution:
- Dequeues import jobs from Redis (RPUSH / BLPOP)
- Runs extraction, analysis, and report generation
- Uploads all raw documents and reports to S3/MinIO/Cloudflare R2
- Updates job state in Redis cache and PostgreSQL
- Purges temporary scratch/staging directories to keep VPS01 disk completely clean
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .import_pipeline import ImportCancelledError, import_folder, staging_root
from .jobs import clear_job_cancel, make_cancel_checker, update_job
from .redis_store import RedisStore

if TYPE_CHECKING:
    from .config import AppConfig
    from .indexer import SearchIndex

logger = logging.getLogger("seamtech_search.worker")

_worker_thread: threading.Thread | None = None
_worker_running: bool = False


def process_import_task(
    payload: dict[str, Any],
    config: AppConfig,
    index: SearchIndex,
    redis_store: RedisStore | None = None,
) -> dict[str, Any]:
    """Execute a single import job payload, upload to S3, and clean up temporary files."""
    job_id = payload["job_id"]
    source_path = Path(payload["source_path"])
    selected_pdf = Path(payload["selected_pdf"]) if payload.get("selected_pdf") else None
    selected_excel = Path(payload["selected_excel"]) if payload.get("selected_excel") else None

    cancel_check = make_cancel_checker(job_id)

    def progress_cb(stage: str, percent: int) -> None:
        update_job(index, job_id, status="running", progress=percent, stage=stage)
        if redis_store and redis_store.is_configured():
            redis_store.update_job(job_id, {"status": "running", "progress": percent, "stage": stage})

    try:
        update_job(index, job_id, status="running", progress=5, stage="starting")
        if redis_store and redis_store.is_configured():
            redis_store.update_job(job_id, {"status": "running", "progress": 5, "stage": "starting"})

        result = import_folder(
            source=source_path,
            config=config,
            index=index,
            selected_pdf=selected_pdf,
            import_id=job_id,
            progress_callback=progress_cb,
            cancel_check=cancel_check,
            selected_excel=selected_excel,
        )

        if cancel_check():
            raise ImportCancelledError("Job was cancelled by user")

        from dataclasses import asdict

        final_payload = asdict(result)
        final_status = "completed" if result.status == "completed" else result.status

        update_job(
            index,
            job_id,
            status=final_status,
            progress=100,
            stage="done",
            result=final_payload,
        )
        if redis_store and redis_store.is_configured():
            redis_store.update_job(
                job_id,
                {"status": final_status, "progress": 100, "stage": "done", "result": final_payload},
            )

        # Ephemeral scratch cleanup: only when every artifact is verified in object
        # storage (or when storage is not configured, where there is nothing to lose).
        # This prevents data loss if an upload fails or is partial.
        staging_dir = staging_root(config)
        try:
            resolved_source = source_path.expanduser().resolve()
            resolved_staging = staging_dir.resolve()
            is_staged = resolved_source.parent == resolved_staging
            should_purge = config.delete_local_after_upload or is_staged
            if should_purge:
                # Gate on all_verified when S3 is configured; otherwise allow purge.
                all_verified = getattr(result, "all_verified", False)
                upload_status = getattr(result, "upload_status", "not_configured")
                if all_verified or upload_status in ("not_configured", "not_applicable", "uploaded"):
                    # For uploaded status we still require all_verified unless storage is not configured.
                    if upload_status == "uploaded" and not all_verified:
                        logger.warning(
                            "Skipping purge of %s: upload_status=uploaded but not all artifacts verified",
                            resolved_source,
                        )
                    else:
                        logger.info(
                            "Purging local staged scratch directory %s to keep VPS disk stateless",
                            resolved_source,
                        )
                        shutil.rmtree(resolved_source, ignore_errors=True)
                else:
                    logger.warning(
                        "Skipping purge of %s: upload_status=%s all_verified=%s — keeping scratch for retry",
                        resolved_source,
                        upload_status,
                        all_verified,
                    )
        except Exception as cleanup_err:
            logger.warning("Failed to purge scratch directory %s: %s", source_path, cleanup_err)

        return final_payload

    except ImportCancelledError:
        update_job(index, job_id, status="cancelled", stage="cancelled", error="Job was cancelled by user")
        if redis_store and redis_store.is_configured():
            redis_store.update_job(job_id, {"status": "cancelled", "stage": "cancelled", "error": "Job was cancelled by user"})
        return {"job_id": job_id, "status": "cancelled"}
    except Exception as exc:
        logger.exception("Import job %s failed: %s", job_id, exc)
        update_job(index, job_id, status="failed", stage="failed", error=str(exc))
        if redis_store and redis_store.is_configured():
            redis_store.update_job(job_id, {"status": "failed", "stage": "failed", "error": str(exc)})
        return {"job_id": job_id, "status": "failed", "error": str(exc)}
    finally:
        clear_job_cancel(job_id)


def worker_loop(config: AppConfig, index: SearchIndex, redis_store: RedisStore) -> None:
    """Continuous polling worker loop consuming tasks from Redis queue."""
    global _worker_running
    _worker_running = True
    logger.info("SEAMTECH Redis background worker started (queue: seamtech:queue:imports)")

    while _worker_running:
        try:
            task = redis_store.dequeue_task("imports", timeout=2)
            if task:
                logger.info("Worker received import task for job %s", task.get("job_id"))
                process_import_task(task, config, index, redis_store)
        except Exception as exc:
            logger.error("Error in Redis worker loop: %s", exc)
            time.sleep(1.0)

    logger.info("SEAMTECH Redis background worker stopped.")


def start_background_worker(config: AppConfig, index: SearchIndex, redis_store: RedisStore) -> None:
    """Start the Redis background worker in a daemon thread if Redis is configured."""
    global _worker_thread
    if not redis_store.is_configured() or not redis_store.ping():
        logger.info("Redis is not configured or offline; background jobs will run in-process.")
        return

    _worker_thread = threading.Thread(
        target=worker_loop,
        args=(config, index, redis_store),
        daemon=True,
        name="seamtech-redis-worker",
    )
    _worker_thread.start()


def stop_background_worker() -> None:
    """Signal the background worker thread to terminate."""
    global _worker_running
    _worker_running = False
