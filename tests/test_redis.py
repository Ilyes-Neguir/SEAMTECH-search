from pathlib import Path
from unittest.mock import MagicMock, patch

from seamtech_search.config import AppConfig
from seamtech_search.redis_store import RedisStore


def test_redis_store_unconfigured() -> None:
    config = AppConfig(root_paths=[Path(".")], min_free_bytes=0)
    store = RedisStore(config=config)
    assert store.is_configured() is False
    assert store.ping() is False


def test_redis_store_configured() -> None:
    config = AppConfig(root_paths=[Path(".")], redis_url="redis://127.0.0.1:6379/0", min_free_bytes=0)
    store = RedisStore(config=config)
    assert store.is_configured() is True


def test_redis_store_job_caching_and_queue() -> None:
    store = RedisStore(redis_url="redis://localhost:6379/0")
    mock_client = MagicMock()
    mock_client.get.return_value = '{"id": "job-1", "status": "running"}'
    mock_client.blpop.return_value = ("seamtech:queue:imports", '{"job_id": "job-1"}')
    mock_client.blmove.return_value = '{"job_id": "job-1"}'

    with patch.object(store, "_get_client", return_value=mock_client):
        assert store.set_job("job-1", {"id": "job-1", "status": "running"}) is True
        mock_client.set.assert_called_once()

        job = store.get_job("job-1")
        assert job == {"id": "job-1", "status": "running"}

        assert store.enqueue_task("imports", {"job_id": "job-1"}) is True
        mock_client.rpush.assert_called_once()

        task = store.dequeue_task("imports")
        assert task == {"job_id": "job-1"}

        # Test new queue ack/retry/deadletter methods
        assert store.ack_task("imports", {"job_id": "job-1"}) is True
        assert store.retry_task("imports", {"job_id": "job-1"}, delay_seconds=1) is True
        assert store.deadletter_task("imports", {"job_id": "job-1"}) is True
        mock_client.llen.return_value = 1
        assert store.get_deadletter_count("imports") == 1
