from __future__ import annotations

import argparse
from collections.abc import Sequence

import uvicorn

from .api import create_app
from .config import AppConfig, default_config_path
from .crawler import crawl
from .indexer import SearchIndex


def main(argv: Sequence[str] | None = None) -> None:
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


def run_index(config_path: str, rebuild: bool = False) -> None:
    config = AppConfig.load(config_path)
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=rebuild)

    seen: set[str] = set()
    scanned = 0
    changed = 0
    for document in crawl(config):
        scanned += 1
        seen.add(document.path_key)
        if index.upsert_document(document):
            changed += 1
        if scanned % 500 == 0:
            print(f"Scanned {scanned} items, updated {changed} items...")

    removed = index.remove_missing(seen)
    print(f"Done. Scanned: {scanned}. Updated: {changed}. Removed: {removed}.")


def run_search(config_path: str, query: str, limit: int = 10) -> None:
    config = AppConfig.load(config_path)
    index = SearchIndex(config.database_path)
    results = index.search(query, limit=limit)
    for result in results:
        print(f"{result['match_type']:>10}  {result['name']}")
        print(f"            {result['path']}")
    print(f"{len(results)} result(s).")


def run_stats(config_path: str) -> None:
    config = AppConfig.load(config_path)
    index = SearchIndex(config.database_path)
    index.initialize()
    stats = index.stats()
    print(f"Documents: {stats.total_documents}")
    print(f"Files:     {stats.files}")
    print(f"Folders:   {stats.folders}")


def run_server(config_path: str) -> None:
    config = AppConfig.load(config_path)
    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port)
