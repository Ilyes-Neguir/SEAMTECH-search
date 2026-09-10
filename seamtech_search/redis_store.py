"""Redis client for distributed background task queueing, state caching, and rate limiting.

Provides:
- Distributed background job queue (RPUSH / BLPOP)
- Fast job status caching with TTL
- Sliding-window rate limiting across multi-worker FastAPI instances
- Graceful degradation if Redis is temporarily unreachable
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import AppConfig

logger = logging.getLogger("seamtech_search.redis_store")


class RedisStore:
    """Redis integration for job queues, status caching, and rate-limiting."""

    def __init__(self, redis_url: str | None = None, config: AppConfig | None = None) -> None:
        if config is not None:
            self.redis_url = config.redis_url
        else:
            self.redis_url = redis_url

        self._client = None

    def is_configured(self) -> bool:
        return bool(self.redis_url)

    def _get_client(self):
        if self._client is not None:
            return self._client

        if not self.redis_url:
            return None

        try:
            import redis

            self._client = redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=2.0,
                socket_timeout=5.0,
            )
            return self._client
        except Exception as exc:
            logger.warning("Could not initialize Redis client for %s: %s", self.redis_url, exc)
            return None

    def ping(self) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            return bool(client.ping())
        except Exception as exc:
            logger.debug("Redis ping failed: %s", exc)
            return False

    # ---------------------------------------------------------------------------
    # Sliding-Window Rate Limiting (Sorted Sets)
    # ---------------------------------------------------------------------------

    def check_rate_limit(self, key: str, limit: int, window_seconds: float = 60.0) -> tuple[bool, int]:
        """Check if request exceeds rate limit using sliding window in Redis.

        Returns (is_limited, retry_after_seconds).
        """
        client = self._get_client()
        if client is None:
            return False, 0

        now = time.time()
        cutoff = now - window_seconds
        redis_key = f"seamtech:ratelimit:{key}"

        try:
            pipe = client.pipeline()
            # Remove timestamps older than window
            pipe.zremrangebyscore(redis_key, 0, cutoff)
            # Count remaining in current window
            pipe.zcard(redis_key)
            # Add current request
            pipe.zadd(redis_key, {f"{now}:{time.perf_counter()}": now})
            # Expire rate limit key after window
            pipe.expire(redis_key, int(window_seconds) + 5)
            # Get the oldest timestamp in current window
            pipe.zrange(redis_key, 0, 0, withscores=True)
            results = pipe.execute()

            current_count = results[1]
            if current_count >= limit:
                oldest_entries = results[4]
                oldest_ts = oldest_entries[0][1] if oldest_entries else cutoff
                retry_after = max(1, int(window_seconds - (now - oldest_ts)) + 1)
                return True, retry_after

            return False, 0
        except Exception as exc:
            logger.warning("Redis rate limit check failed, falling back: %s", exc)
            return False, 0

    # ---------------------------------------------------------------------------
    # Job Status Caching & Distribution
    # ---------------------------------------------------------------------------

    def set_job(self, job_id: str, data: dict[str, Any], ttl_seconds: int = 86400) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            client.set(f"seamtech:job:{job_id}", json.dumps(data, ensure_ascii=False), ex=ttl_seconds)
            return True
        except Exception as exc:
            logger.warning("Failed to cache job in Redis %s: %s", job_id, exc)
            return False

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        client = self._get_client()
        if client is None:
            return None
        try:
            val = client.get(f"seamtech:job:{job_id}")
            if val is None:
                return None
            return json.loads(val)
        except Exception as exc:
            logger.warning("Failed to get job from Redis %s: %s", job_id, exc)
            return None

    def update_job(self, job_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        client = self._get_client()
        if client is None:
            return None
        try:
            current = self.get_job(job_id) or {"id": job_id}
            current.update(updates)
            self.set_job(job_id, current)
            return current
        except Exception as exc:
            logger.warning("Failed to update job in Redis %s: %s", job_id, exc)
            return None

    # ---------------------------------------------------------------------------
    # Task Queue (RPUSH / BLPOP)
    # ---------------------------------------------------------------------------

    def enqueue_task(self, queue_name: str, payload: dict[str, Any]) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            client.rpush(f"seamtech:queue:{queue_name}", json.dumps(payload, ensure_ascii=False))
            return True
        except Exception as exc:
            logger.error("Failed to enqueue task to Redis %s: %s", queue_name, exc)
            return False

    def dequeue_task(self, queue_name: str, timeout: int = 2) -> dict[str, Any] | None:
        client = self._get_client()
        if client is None:
            return None
        try:
            res = client.blpop(f"seamtech:queue:{queue_name}", timeout=timeout)
            if res:
                _, item = res
                return json.loads(item)
            return None
        except Exception as exc:
            logger.error("Failed to dequeue task from Redis %s: %s", queue_name, exc)
            return None
