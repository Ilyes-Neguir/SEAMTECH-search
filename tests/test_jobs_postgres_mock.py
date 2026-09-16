"""Cover jobs.py postgres branches."""

from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.jobs import create_job, get_job, update_job, cancel_job, recover_stale_jobs


def make_cfg(tmp_path: Path):
    return AppConfig(root_paths=[tmp_path], database_path=tmp_path / "db.db", min_free_bytes=0)


def test_jobs_postgres_branches(tmp_path: Path):
    # Mock SearchIndex to be postgres
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = None
    mock_cursor.rowcount = 1
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_conn.cursor.return_value.__exit__.return_value = None

    idx = SearchIndex(tmp_path / "unused.db", database_url="postgresql://user:pass@localhost/db")
    # Patch is_postgres property to True
    with patch.object(SearchIndex, "is_postgres", new_callable=lambda: property(lambda self: True)):
        with patch.object(idx, "connect") as mock_connect:
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_connect.return_value.__exit__.return_value = None

            # create_job postgres
            mock_cursor.rowcount = 1
            job = create_job(idx, "jid", "/tmp/src")
            assert job["id"] == "jid"

            # get_job postgres - not found
            mock_cursor.fetchone.return_value = None
            assert get_job(idx, "jid") is None

            # get_job postgres - found
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            mock_cursor.fetchone.return_value = {
                "id": "jid",
                "status": "running",
                "progress": 10,
                "stage": "scanning",
                "source_path": "/tmp/src",
                "error": None,
                "result": None,
                "created_at": now,
                "updated_at": now,
            }
            # Need RealDictCursor mock - our mock returns dict
            fetched = get_job(idx, "jid")
            assert fetched is not None

            # update_job postgres with result
            mock_cursor.rowcount = 1
            mock_cursor.fetchone.return_value = {
                "id": "jid",
                "status": "running",
                "progress": 10,
                "stage": "scanning",
                "source_path": "/tmp/src",
                "error": None,
                "result": None,
                "created_at": now,
                "updated_at": now,
            }
            updated = update_job(idx, "jid", status="running", progress=20)
            assert updated is not None

            # update_job with no fields
            updated2 = update_job(idx, "jid")
            assert updated2 is not None

            # update_job rowcount 0 and existing None -> None
            mock_cursor.rowcount = 0
            mock_cursor.fetchone.return_value = None
            assert update_job(idx, "nonexist", status="failed") is None

            # update_job rowcount 0 but existing found -> return existing
            mock_cursor.fetchone.return_value = {
                "id": "jid",
                "status": "cancelled",
                "progress": 0,
                "stage": "cancelled",
                "source_path": "/tmp/src",
                "error": "cancelled",
                "result": None,
                "created_at": now,
                "updated_at": now,
            }
            # For this, get_job will be called inside update_job when rowcount 0, so we need fetchone to return something
            # Our mock currently returns cancelled job
            res = update_job(idx, "jid", status="running")
            assert res is not None

            # cancel_job
            mock_cursor.rowcount = 1
            mock_cursor.fetchone.return_value = {
                "id": "jid",
                "status": "cancelled",
                "progress": 0,
                "stage": "cancelled",
                "source_path": "/tmp/src",
                "error": "cancelled",
                "result": None,
                "created_at": now,
                "updated_at": now,
            }
            cancelled = cancel_job(idx, "jid")
            assert cancelled is not None

            # recover_stale_jobs postgres
            mock_cursor.rowcount = 2
            count = recover_stale_jobs(idx, heartbeat_threshold_seconds=100)
            assert count == 2
