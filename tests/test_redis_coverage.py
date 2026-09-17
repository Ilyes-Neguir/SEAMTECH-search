"""Coverage for redis_store.py — rate limit, cancel, heartbeat, queue."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

from seamtech_search.redis_store import RedisStore


def test_rate_limit_cancel_heartbeat():
    store = RedisStore(redis_url="redis://localhost:6379/0")
    mock = MagicMock()
    mock.zcard.return_value = 0
    mock.zadd.return_value = 1
    mock.zremrangebyscore.return_value = 0
    mock.set.return_value = True
    mock.exists.return_value = 1
    mock.delete.return_value = 1
    mock.get.return_value = None

    with patch.object(store, "_get_client", return_value=mock):
        # Mock pipeline for rate limit
        pipe_mock = MagicMock()
        pipe_mock.execute.return_value = [0, 0, 1, 1, []]
        mock.pipeline.return_value = pipe_mock
        limited, retry = store.check_rate_limit("key", 10)
        assert limited is False
        assert store.set_cancel_flag("job-1") is True
        assert store.is_cancelled("job-1") is True
        assert store.clear_cancel_flag("job-1") is True
        assert store.set_heartbeat("job-1") is True
        mock.get.return_value = json.dumps({"id": "job-1"})
        assert store.set_job("job-1", {"id": "job-1"}) is True
        job = store.get_job("job-1")
        assert job["id"] == "job-1"
        mock.get.return_value = json.dumps({"id": "job-1", "status": "running"})
        updated = store.update_job("job-1", {"status": "completed"})
        assert isinstance(updated, dict)
        assert updated["status"] == "completed"

    mock2 = MagicMock()
    pipe2 = MagicMock()
    pipe2.execute.return_value = [0, 15, 1, 1, [(b"old", time.time() - 10)]]
    mock2.pipeline.return_value = pipe2
    with patch.object(store, "_get_client", return_value=mock2):
        limited, retry = store.check_rate_limit("key", 10)
        assert limited is True
        assert retry >= 1


def test_queue_full():
    store = RedisStore(redis_url="redis://localhost:6379/0")
    mock = MagicMock()
    mock.rpush.return_value = 1
    mock.lrem.return_value = 1
    mock.lrange.return_value = [json.dumps({"job_id": "job-1"})]
    mock.zadd.return_value = 1
    mock.zrangebyscore.return_value = []
    mock.zrem.return_value = 1
    mock.llen.return_value = 2
    mock.lrange.return_value = [json.dumps({"job_id": "job-1"}), json.dumps({"job_id": "job-2"})]

    with patch.object(store, "_get_client", return_value=mock):
        assert store.enqueue_task("imports", {"job_id": "job-1"}) is True
        assert store.ack_task("imports", {"job_id": "job-1"}) is True
        assert store.retry_task("imports", {"job_id": "job-1"}, delay_seconds=1) is True
        assert store.retry_task("imports", {"job_id": "job-1"}, delay_seconds=0) is True
        assert store.deadletter_task("imports", {"job_id": "job-1"}) is True
        assert store.get_deadletter_count("imports") == 2
        dead = store.get_deadletters("imports", limit=10)
        assert len(dead) == 2
        mock.lrange.return_value = [json.dumps({"job_id": "job-1"})]
        mock.rpush.return_value = 1
        mock.lrem.return_value = 1
        assert store.replay_deadletters("imports", limit=1) == 1
        mock.zrangebyscore.return_value = [json.dumps({"job_id": "job-1"})]
        assert store.process_retry_queue("imports") >= 0


def test_dequeue_blmove_blpop():
    store = RedisStore(redis_url="redis://localhost:6379/0")
    mock = MagicMock()
    mock.blmove.return_value = json.dumps({"job_id": "job-1"})
    with patch.object(store, "_get_client", return_value=mock):
        task = store.dequeue_task("imports", timeout=1)
        assert task["job_id"] == "job-1"

    mock2 = MagicMock()
    mock2.blmove.side_effect = AttributeError("no blmove")
    mock2.blpop.return_value = ("seamtech:queue:imports", json.dumps({"job_id": "job-2"}))
    with patch.object(store, "_get_client", return_value=mock2):
        task2 = store.dequeue_task("imports", timeout=1)
        assert task2["job_id"] == "job-2"

    mock3 = MagicMock()
    mock3.blmove.return_value = None
    mock3.blpop.return_value = None
    with patch.object(store, "_get_client", return_value=mock3):
        task3 = store.dequeue_task("imports", timeout=1)
        assert task3 is None


def test_redis_unconfigured_and_ping():
    from pathlib import Path

    from seamtech_search.config import AppConfig
    cfg = AppConfig(root_paths=[Path(".")], min_free_bytes=0)
    store = RedisStore(config=cfg)
    assert store.is_configured() is False
    assert store.ping() is False
    assert store.get_job("x") is None
    assert store.set_job("x", {}) is False
    assert store.enqueue_task("q", {}) is False
    assert store.dequeue_task("q") is None
