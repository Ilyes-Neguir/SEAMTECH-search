from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import AppConfig
from .extractors import extract_text
from .indexer import SearchIndex


def create_app(config: AppConfig) -> FastAPI:
    app = FastAPI(title="SEAMTECH Search", version="0.1.0")
    index = SearchIndex(config.database_path, config.database_url)
    static_dir = Path(__file__).parent / "static"
    metrics = {
        "search_requests": 0,
        "search_errors": 0,
        "last_search_seconds": 0.0,
        "slowest_search_seconds": 0.0,
    }

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def home() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/health")
    def health() -> dict[str, object]:
        index.initialize()
        stats = index.stats()
        health_details = index.health_details()
        disk_path = config.database_path.parent if not index.is_postgres else Path.cwd()
        disk_usage = shutil.disk_usage(disk_path)
        return {
            "status": "ok",
            **health_details,
            "disk_free_bytes": disk_usage.free,
            "disk_total_bytes": disk_usage.total,
            "documents": stats.total_documents,
            "files": stats.files,
            "folders": stats.folders,
        }

    @app.get("/search")
    def search(
        q: str = Query(..., min_length=1),
        limit: int = Query(50, ge=1, le=200),
    ) -> dict[str, object]:
        try:
            started_at = time.perf_counter()
            results = index.search(q, limit=limit)
            elapsed = time.perf_counter() - started_at
            metrics["search_requests"] += 1
            metrics["last_search_seconds"] = elapsed
            metrics["slowest_search_seconds"] = max(float(metrics["slowest_search_seconds"]), elapsed)
        except Exception as exc:
            metrics["search_errors"] += 1
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"query": q, "count": len(results), "results": results}

    @app.get("/metrics")
    def get_metrics() -> dict[str, object]:
        return {
            **metrics,
            "health": health(),
        }

    @app.get("/preview")
    def preview(path: str = Query(..., min_length=1)) -> dict[str, object]:
        target = _validated_path(path, config)
        if target.is_dir():
            children = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
            return {
                "path": str(target),
                "name": target.name,
                "is_dir": True,
                "children": [
                    {
                        "name": child.name,
                        "path": str(child),
                        "is_dir": child.is_dir(),
                        "size": _safe_size(child),
                    }
                    for child in children[:200]
                ],
            }

        text = extract_text(target, max_chars=25_000)
        return {
            "path": str(target),
            "name": target.name,
            "is_dir": False,
            "extension": target.suffix.lower(),
            "size": target.stat().st_size,
            "text": text or "Preview is not available for this file type. Use Open File instead.",
        }

    @app.post("/open")
    def open_path(path: str = Query(..., min_length=1)) -> dict[str, object]:
        target = _validated_path(path, config)
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except AttributeError as exc:
            raise HTTPException(status_code=501, detail="Open path is only supported on Windows hosts.") from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"opened": str(target), "is_dir": target.is_dir()}

    return app


def _validated_path(path: str, config: AppConfig) -> Path:
    target = Path(path).expanduser().resolve()
    allowed_roots = [root.resolve() for root in config.root_paths]
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path does not exist.")
    if not any(target == root or root in target.parents for root in allowed_roots):
        raise HTTPException(status_code=403, detail="Path is outside configured search roots.")
    return target


def _safe_size(path: Path) -> int:
    if path.is_dir():
        return 0
    try:
        return path.stat().st_size
    except OSError:
        return 0
