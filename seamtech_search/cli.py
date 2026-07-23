from __future__ import annotations

import argparse
from collections.abc import Sequence

import uvicorn

from .api import create_app
from .config import AppConfig
from .crawler import crawl
from .indexer import SearchIndex


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="seamtech-search")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Scan folders and update the search index")
    index_parser.add_argument("--config", default="config.json")
    index_parser.add_argument("--rebuild", action="store_true")

    serve_parser = subparsers.add_parser("serve", help="Run the FastAPI web server")
    serve_parser.add_argument("--config", default="config.json")

    args = parser.parse_args(argv)

    if args.command == "index":
        run_index(args.config, rebuild=args.rebuild)
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


def run_server(config_path: str) -> None:
    config = AppConfig.load(config_path)
    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port)

