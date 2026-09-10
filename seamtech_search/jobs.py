"""Background job management and cooperative cancellation.

Manages the import_jobs table:
- Async job registration & polling (POST /imports -> 202 -> GET /imports/{id})
- Cooperative cancellation flags
- Progress reporting & stage transitions
- Stale job recovery on server startup
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .indexer import SearchIndex

logger = logging.getLogger("seamtech_search.jobs")

_cancellation_lock = threading.Lock()
_cancelled_job_ids: set[str] = set()


class ImportCancelledError(Exception):
    """Raised when an import job is cooperatively cancelled during execution."""


def register_job_cancel(job_id: str) -> None:
    """Flag a job ID as cancelled in memory for active worker threads."""
    with _cancellation_lock:
        _cancelled_job_ids.add(job_id)


def is_job_cancelled(job_id: str) -> bool:
    """Check whether a job has received a cancellation request."""
    with _cancellation_lock:
        return job_id in _cancelled_job_ids


def clear_job_cancel(job_id: str) -> None:
    """Clean up cancellation flag once job finishes or exits."""
    with _cancellation_lock:
        _cancelled_job_ids.discard(job_id)


def make_cancel_checker(job_id: str) -> Callable[[], bool]:
    """Return a zero-argument callable that returns True if job_id was cancelled."""
    return lambda: is_job_cancelled(job_id)


def create_job(
    index: SearchIndex,
    job_id: str,
    source_path: str,
    status: str = "pending",
    stage: str = "queued",
) -> dict[str, Any]:
    """Create a new job record in import_jobs."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with index.connect() as conn:
        if index.is_postgres:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO import_jobs (
                        id, status, progress, stage, source_path, error, result, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now())
                    """,
                    (job_id, status, 0, stage, source_path, None, None),
                )
        else:
            conn.execute(
                """
                INSERT INTO import_jobs (
                    id, status, progress, stage, source_path, error, result, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, status, 0, stage, source_path, None, None, now_iso, now_iso),
            )

    return {
        "id": job_id,
        "status": status,
        "progress": 0,
        "stage": stage,
        "source_path": source_path,
        "error": None,
        "result": None,
        "created_at": now_iso,
        "updated_at": now_iso,
    }


def update_job(
    index: SearchIndex,
    job_id: str,
    status: str | None = None,
    progress: int | None = None,
    stage: str | None = None,
    error: str | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Update fields of an active job."""
    if is_job_cancelled(job_id) and status != "cancelled":
        return get_job(index, job_id)

    now_iso = datetime.now(timezone.utc).isoformat()
    fields: list[str] = []
    values: list[Any] = []

    if status is not None:
        fields.append("status")
        values.append(status)
    if progress is not None:
        fields.append("progress")
        values.append(progress)
    if stage is not None:
        fields.append("stage")
        values.append(stage)
    if error is not None:
        fields.append("error")
        values.append(error)
    if result is not None:
        fields.append("result")
        values.append(json.dumps(result, ensure_ascii=False))

    if not fields:
        return get_job(index, job_id)

    with index.connect() as conn:
        if index.is_postgres:
            import psycopg2.extras

            set_clauses = [f"{f} = %s" for f in fields]
            set_clauses.append("updated_at = now()")
            extra_where = "" if status == "cancelled" else " AND status != 'cancelled'"
            sql = f"UPDATE import_jobs SET {', '.join(set_clauses)} WHERE id = %s{extra_where}"

            pg_values = []
            for f, v in zip(fields, values):
                if f == "result" and result is not None:
                    pg_values.append(psycopg2.extras.Json(result))
                else:
                    pg_values.append(v)
            pg_values.append(job_id)

            with conn.cursor() as cursor:
                cursor.execute(sql, pg_values)
        else:
            set_clauses = [f"{f} = ?" for f in fields]
            set_clauses.append("updated_at = ?")
            extra_where = "" if status == "cancelled" else " AND status != 'cancelled'"
            sql = f"UPDATE import_jobs SET {', '.join(set_clauses)} WHERE id = ?{extra_where}"
            sqlite_values = list(values) + [now_iso, job_id]
            conn.execute(sql, sqlite_values)

    return get_job(index, job_id)


def get_job(index: SearchIndex, job_id: str) -> dict[str, Any] | None:
    """Retrieve job details by job_id."""
    with index.connect() as conn:
        if index.is_postgres:
            import psycopg2.extras

            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT id, status, progress, stage, source_path, error, result, created_at, updated_at
                    FROM import_jobs WHERE id = %s
                    """,
                    (job_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                data = dict(row)
                if isinstance(data.get("created_at"), datetime):
                    data["created_at"] = data["created_at"].isoformat()
                if isinstance(data.get("updated_at"), datetime):
                    data["updated_at"] = data["updated_at"].isoformat()
                return data

        cursor = conn.execute(
            """
            SELECT id, status, progress, stage, source_path, error, result, created_at, updated_at
            FROM import_jobs WHERE id = ?
            """,
            (job_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        res_val = row["result"]
        parsed_result = None
        if res_val:
            try:
                parsed_result = json.loads(res_val) if isinstance(res_val, str) else res_val
            except Exception:
                parsed_result = None
        return {
            "id": row["id"],
            "status": row["status"],
            "progress": row["progress"],
            "stage": row["stage"],
            "source_path": row["source_path"],
            "error": row["error"],
            "result": parsed_result,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


def cancel_job(index: SearchIndex, job_id: str) -> dict[str, Any] | None:
    """Request cooperative cancellation of a job."""
    register_job_cancel(job_id)
    return update_job(
        index,
        job_id,
        status="cancelled",
        stage="cancelled",
        error="Job was cancelled by user",
    )


def recover_stale_jobs(index: SearchIndex) -> int:
    """Mark running/pending jobs from prior crashed/restarted processes as failed."""
    with index.connect() as conn:
        if index.is_postgres:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE import_jobs
                    SET status = 'failed',
                        stage = 'failed',
                        error = 'Server restarted while job was running',
                        updated_at = now()
                    WHERE status IN ('pending', 'running')
                    """
                )
                count = cursor.rowcount
        else:
            now_iso = datetime.now(timezone.utc).isoformat()
            cursor = conn.execute(
                """
                UPDATE import_jobs
                SET status = 'failed',
                    stage = 'failed',
                    error = 'Server restarted while job was running',
                    updated_at = ?
                WHERE status IN ('pending', 'running')
                """,
                (now_iso,),
            )
            count = cursor.rowcount

    if count > 0:
        logger.warning("Recovered %d stale import job(s) left in running/pending state.", count)
    return max(0, count)
