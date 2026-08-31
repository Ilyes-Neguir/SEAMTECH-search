from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import AppConfig
from .indexer import SearchIndex


def create_app(config: AppConfig) -> FastAPI:
    app = FastAPI(title="SEAMTECH Search", version="0.1.0")
    index = SearchIndex(config.database_path)
    static_dir = Path(__file__).parent / "static"

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def home() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/health")
    def health() -> dict[str, object]:
        index.initialize()
        stats = index.stats()
        return {
            "status": "ok",
            "database_path": str(config.database_path),
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
            results = index.search(q, limit=limit)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"query": q, "count": len(results), "results": results}

    return app
