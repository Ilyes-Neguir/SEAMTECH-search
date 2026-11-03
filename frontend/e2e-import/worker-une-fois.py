"""Worker réel, dans un processus séparé, pour le banc de suivi isolé."""
from __future__ import annotations

import sys
from pathlib import Path

from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.redis_store import RedisStore
from seamtech_search.worker import worker_loop


def main() -> None:
    config = AppConfig.model_validate_json(Path(sys.argv[1]).read_text(encoding="utf-8"))
    index = SearchIndex(config.database_path, config.database_url)
    try:
        index.initialize()
        index.run_migrations()
        redis = RedisStore(config=config)
        if not redis.is_configured() or not redis.ping():
            raise RuntimeError("Redis isolé indisponible")
        worker_loop(config, index, redis, worker_id="worker-e2e-suivi", run_once=True)
    finally:
        index.close()


if __name__ == "__main__":
    main()
