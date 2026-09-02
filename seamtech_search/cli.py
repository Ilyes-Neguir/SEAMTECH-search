from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Sequence

import uvicorn

from .api import create_app
from .config import AppConfig, default_config_path
from .crawler import ScanIncompleteError, crawl
from .indexer import SearchIndex

LOGGER = logging.getLogger("seamtech_search")
BATCH_SIZE = 250


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="seamtech-search")
    subparsers = parser.add_subparsers(dest="command", required=True)

    default_config = default_config_path()

    index_parser = subparsers.add_parser("index", help="Scan folders and update the search index")
    index_parser.add_argument("--config", default=str(default_config))
    index_parser.add_argument("--rebuild", action="store_true")

    search_parser = subparsers.add_parser("search", help="Search the local index from the terminal")
    search_parser.add_argument("query")
    search_parser.add_argument("--config", default=str(default_config))
    search_parser.add_argument("--limit", type=int, default=10)

    stats_parser = subparsers.add_parser("stats", help="Show index statistics")
    stats_parser.add_argument("--config", default=str(default_config))

    serve_parser = subparsers.add_parser("serve", help="Run the FastAPI web server")
    serve_parser.add_argument("--config", default=str(default_config))

    args = parser.parse_args(argv)

    if args.command == "index":
        run_index(args.config, rebuild=args.rebuild)
    elif args.command == "search":
        run_search(args.config, args.query, args.limit)
    elif args.command == "stats":
        run_stats(args.config)
    elif args.command == "serve":
        run_server(args.config)


def run_index(config_path: str, rebuild: bool = False) -> dict[str, float | int]:
    config = AppConfig.load(config_path)
    index = SearchIndex(config.database_path, config.database_url)
    with index.scan_lock():
        with index.scan_snapshot():
            return _run_index(index, config, rebuild)


def _run_index(index: SearchIndex, config: AppConfig, rebuild: bool = False) -> dict[str, float | int]:
    index.initialize(rebuild=rebuild)

    seen: set[str] = set()
    batch = []
    scanned = 0
    changed = 0
    started_at = time.perf_counter()
    scan_id = index.start_scan()
    existing_metadata = index.existing_metadata()
    try:
        for document in crawl(config, existing_metadata):
            scanned += 1
            seen.add(document.path_key)
            batch.append(document)
            if len(batch) >= BATCH_SIZE:
                changed += index.upsert_documents(batch)
                batch.clear()
            if scanned % 500 == 0:
                elapsed = max(time.perf_counter() - started_at, 0.001)
                LOGGER.info("Scanned %s items, updated %s items, %.1f items/sec", scanned, changed, scanned / elapsed)
    except Exception as exc:
        index.finish_scan(scan_id, "failed", scanned, changed, 0, type(exc).__name__ + ": " + str(exc))
        if isinstance(exc, ScanIncompleteError):
            LOGGER.exception("Indexing aborted because the scan was incomplete; existing index was preserved")
        else:
            LOGGER.exception("Indexing failed; existing index was preserved")
        raise

    if batch:
        changed += index.upsert_documents(batch)

    removed = index.remove_missing(seen, scan_complete=True)
    index.finish_scan(scan_id, "completed", scanned, changed, removed)
    elapsed = max(time.perf_counter() - started_at, 0.001)
    LOGGER.info(
        "Indexing complete. scanned=%s updated=%s removed=%s elapsed_seconds=%.2f items_per_second=%.1f",
        scanned,
        changed,
        removed,
        elapsed,
        scanned / elapsed,
    )
    return {
        "scanned": scanned,
        "changed": changed,
        "removed": removed,
        "elapsed_seconds": round(elapsed, 3),
        "items_per_second": round(scanned / elapsed, 2),
    }


def run_search(config_path: str, query: str, limit: int = 10) -> None:
    config = AppConfig.load(config_path)
    index = SearchIndex(config.database_path, config.database_url)
    results = index.search(query, limit=limit)
    for result in results:
        print(f"{result['match_type']:>10}  {result['name']}")
        print(f"            {result['path']}")
    print(f"{len(results)} result(s).")


def run_stats(config_path: str) -> None:
    config = AppConfig.load(config_path)
    index = SearchIndex(config.database_path, config.database_url)
    index.initialize()
    stats = index.stats()
    print(f"Documents: {stats.total_documents}")
    print(f"Files:     {stats.files}")
    print(f"Folders:   {stats.folders}")


def run_server(config_path: str) -> None:
    config = AppConfig.load(config_path)
    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port)
