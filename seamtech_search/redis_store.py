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
    # Cancellation (Redis with DB fallback) — 4.5
    # ---------------------------------------------------------------------------

    def set_cancel_flag(self, job_id: str, ttl_seconds: int = 86400) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            client.set(f"seamtech:cancel:{job_id}", "1", ex=ttl_seconds)
            return True
        except Exception as exc:
            logger.warning("Failed to set cancel flag in Redis %s: %s", job_id, exc)
            return False

    def is_cancelled(self, job_id: str) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            return bool(client.exists(f"seamtech:cancel:{job_id}"))
        except Exception:
            return False

    def clear_cancel_flag(self, job_id: str) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            client.delete(f"seamtech:cancel:{job_id}")
            return True
        except Exception:
            return False

    # ---------------------------------------------------------------------------
    # Heartbeat — 4.7
    # ---------------------------------------------------------------------------

    def set_heartbeat(self, job_id: str, ttl_seconds: int = 300) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            client.set(f"seamtech:heartbeat:{job_id}", str(time.time()), ex=ttl_seconds)
            return True
        except Exception:
            return False

    def get_heartbeat(self, job_id: str) -> float | None:
        client = self._get_client()
        if client is None:
            return None
        try:
            val = client.get(f"seamtech:heartbeat:{job_id}")
            return float(val) if val else None
        except Exception:
            return None

    # ---------------------------------------------------------------------------
    # Task Queue with acknowledgement, retry and dead-letter — 4.6
    # ---------------------------------------------------------------------------

    def enqueue_task(self, queue_name: str, payload: dict[str, Any]) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            # Include attempt count
            payload = dict(payload)
            payload.setdefault("attempt", 0)
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
            # Use BLMOVE to move from queue to processing list for ack semantics
            # Fallback to BLPOP if BLMOVE not available (older redis-py)
            processing_key = f"seamtech:processing:{queue_name}"
            try:
                item = client.blmove(f"seamtech:queue:{queue_name}", processing_key, timeout=timeout)
                if item:
                    return json.loads(item)
                return None
            except AttributeError:
                # blmove not available, fallback
                res = client.blpop(f"seamtech:queue:{queue_name}", timeout=timeout)
                if res:
                    _, item = res
                    # Also push to processing for tracking
                    try:
                        client.rpush(processing_key, item)
                    except Exception:
                        pass
                    return json.loads(item)
                return None
        except Exception as exc:
            logger.error("Failed to dequeue task from Redis %s: %s", queue_name, exc)
            return None

    def ack_task(self, queue_name: str, payload: dict[str, Any]) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            processing_key = f"seamtech:processing:{queue_name}"
            # Remove one occurrence of this payload from processing list
            # We need to match the exact JSON; simpler: lrem by value
            item = json.dumps(payload, ensure_ascii=False)
            # Try to remove the exact item, if not found try with attempt field variations
            removed = client.lrem(processing_key, 1, item)
            if removed == 0:
                # Try to remove any item with same job_id
                job_id = payload.get("job_id")
                if job_id:
                    # Scan processing list for job_id
                    items = client.lrange(processing_key, 0, -1)
                    for it in items:
                        try:
                            data = json.loads(it)
                            if data.get("job_id") == job_id:
                                client.lrem(processing_key, 1, it)
                                break
                        except Exception:
                            continue
            return True
        except Exception as exc:
            logger.warning("Failed to ack task %s: %s", queue_name, exc)
            return False

    def retry_task(self, queue_name: str, payload: dict[str, Any], delay_seconds: int = 0) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            retry_key = f"seamtech:retry:{queue_name}"
            # Remove from processing
            self.ack_task(queue_name, payload)
            # Increment attempt
            new_payload = dict(payload)
            new_payload["attempt"] = new_payload.get("attempt", 0) + 1
            if delay_seconds > 0:
                score = time.time() + delay_seconds
                client.zadd(retry_key, {json.dumps(new_payload, ensure_ascii=False): score})
            else:
                client.rpush(f"seamtech:queue:{queue_name}", json.dumps(new_payload, ensure_ascii=False))
            return True
        except Exception as exc:
            logger.error("Failed to retry task %s: %s", queue_name, exc)
            return False

    def deadletter_task(self, queue_name: str, payload: dict[str, Any]) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            dead_key = f"seamtech:deadletter:{queue_name}"
            self.ack_task(queue_name, payload)
            client.rpush(dead_key, json.dumps(payload, ensure_ascii=False))
            return True
        except Exception as exc:
            logger.error("Failed to deadletter task %s: %s", queue_name, exc)
            return False

    def get_deadletter_count(self, queue_name: str) -> int:
        client = self._get_client()
        if client is None:
            return 0
        try:
            return int(client.llen(f"seamtech:deadletter:{queue_name}"))
        except Exception:
            return 0

    def get_deadletters(self, queue_name: str, limit: int = 100) -> list[dict[str, Any]]:
        client = self._get_client()
        if client is None:
            return []
        try:
            items = client.lrange(f"seamtech:deadletter:{queue_name}", 0, limit - 1)
            return [json.loads(i) for i in items]
        except Exception:
            return []

    def replay_deadletters(self, queue_name: str, limit: int = 100) -> int:
        client = self._get_client()
        if client is None:
            return 0
        try:
            dead_key = f"seamtech:deadletter:{queue_name}"
            queue_key = f"seamtech:queue:{queue_name}"
            count = 0
            for _ in range(limit):
                item = client.lpop(dead_key)
                if not item:
                    break
                client.rpush(queue_key, item)
                count += 1
            return count
        except Exception as exc:
            logger.error("Failed to replay deadletters %s: %s", queue_name, exc)
            return 0

    def process_retry_queue(self, queue_name: str) -> int:
        client = self._get_client()
        if client is None:
            return 0
        try:
            retry_key = f"seamtech:retry:{queue_name}"
            queue_key = f"seamtech:queue:{queue_name}"
            now = time.time()
            items = client.zrangebyscore(retry_key, 0, now, start=0, num=100)
            if not items:
                return 0
            pipe = client.pipeline()
            for item in items:
                pipe.zrem(retry_key, item)
                pipe.rpush(queue_key, item)
            pipe.execute()
            return len(items)
        except Exception as exc:
            logger.warning("Failed to process retry queue %s: %s", queue_name, exc)
            return 0
