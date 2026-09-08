from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from .config import AppConfig
from .extractors import extract_file
from .indexer import SearchIndex
from .import_pipeline import (
    ImportResult,
    correct_import,
    get_import,
    import_folder,
    retry_upload,
    scan_folder,
    staging_root,
)


class ImportRequest(BaseModel):
    source_path: str = Field(min_length=1, max_length=4_096)


class ImportScanRequest(BaseModel):
    source_path: str = Field(min_length=1, max_length=4_096)


class ImportConfirmRequest(BaseModel):
    source_path: str = Field(min_length=1, max_length=4_096)
    technical_pdf: str = Field(min_length=1, max_length=4_096)


class DimensionsCorrection(BaseModel):
    length: float | None = None
    width: float | None = None
    height: float | None = None
    unit: str | None = Field(default=None, max_length=8)


class ImportCorrectionRequest(BaseModel):
    reference: str | None = Field(default=None, max_length=500)
    material: str | None = Field(default=None, max_length=500)
    quantity: int | None = Field(default=None, ge=0)
    description: str | None = Field(default=None, max_length=2_000)
    dimensions: DimensionsCorrection | None = None


def _import_payload(result: ImportResult) -> dict[str, Any]:
    payload = asdict(result)
    return payload


def create_app(config: AppConfig) -> FastAPI:
    app = FastAPI(title="SEAMTECH Search", version="0.2.0")
    index = SearchIndex(config.database_path, config.database_url)
    index.initialize()
    metrics = {
        "search_requests": 0,
        "search_errors": 0,
        "last_search_seconds": 0.0,
        "slowest_search_seconds": 0.0,
    }
    @app.get("/")
    def root(token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None) -> dict[str, str]:
        _require_auth(config, token)
        return {"service": "seamtech-search-api", "health": "/health", "docs": "/docs"}

    @app.get("/health")
    def health(token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None) -> dict[str, object]:
        _require_auth(config, token)
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
            "last_scan": index.latest_scan(),
        }

    @app.get("/search")
    def search(
        q: str = Query(..., min_length=1, max_length=500),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0, le=1_000_000),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        try:
            started_at = time.perf_counter()
            raw_results = index.search(q, limit=limit + 1, offset=offset)
            results = raw_results[:limit]
            has_more = len(raw_results) > limit
            elapsed = time.perf_counter() - started_at
            metrics["search_requests"] += 1
            metrics["last_search_seconds"] = elapsed
            metrics["slowest_search_seconds"] = max(float(metrics["slowest_search_seconds"]), elapsed)
        except (ValueError, RuntimeError) as exc:
            metrics["search_errors"] += 1
            raise HTTPException(status_code=400, detail="Invalid search query.") from exc
        except Exception as exc:
            metrics["search_errors"] += 1
            raise HTTPException(status_code=500, detail="Search service failure.") from exc
        return {"query": q, "count": len(results), "offset": offset, "limit": limit, "has_more": has_more, "results": results}

    @app.get("/metrics")
    def get_metrics(token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None) -> dict[str, object]:
        _require_auth(config, token)
        return {**metrics, "health": health(token)}

    @app.get("/preview")
    def preview(
        path: str = Query(..., min_length=1, max_length=4_096),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        target = _validated_path(path, config)
        if target.is_dir():
            children = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
            return {
                "path": str(target),
                "name": target.name,
                "is_dir": True,
                "extraction_status": "not_applicable",
                "extraction_detail": "",
                "children": [
                    {"name": child.name, "path": str(child), "is_dir": child.is_dir(), "size": _safe_size(child)}
                    for child in children[:200]
                ],
            }
        extraction = extract_file(
            target,
            max_chars=25_000,
            max_file_size_bytes=config.max_file_size_bytes,
            enable_legacy_office=config.enable_legacy_office,
            libreoffice_command=config.libreoffice_command,
            enable_ocr=config.enable_ocr,
            tesseract_command=config.tesseract_command,
            ocrmypdf_command=config.ocrmypdf_command,
            external_extraction_timeout_seconds=config.external_extraction_timeout_seconds,
            external_extractors=config.external_extractors,
        )
        return {
            "path": str(target),
            "name": target.name,
            "is_dir": False,
            "extension": target.suffix.lower(),
            "size": target.stat().st_size,
            "text": extraction.text,
            "extraction_status": extraction.status,
            "extraction_detail": extraction.detail,
        }

    @app.post("/open")
    def open_path(
        path: str = Query(..., min_length=1, max_length=4_096),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        target = _validated_path(path, config)
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except AttributeError as exc:
            raise HTTPException(status_code=501, detail="Open path is only supported on Windows hosts.") from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail="The operating system could not open this path.") from exc
        return {"opened": str(target), "is_dir": target.is_dir()}

    @app.post("/imports")
    async def create_import(
        request: ImportRequest,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        try:
            # PDF parsing, report generation, indexing, and Graph uploads are
            # synchronous and may include retry back-off. Keep them off the
            # event loop so imports do not block unrelated async requests.
            result = await asyncio.to_thread(import_folder, Path(request.source_path), config, index)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _import_payload(result)

    @app.post("/imports/scan")
    async def scan_import(
        request: ImportScanRequest,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        """Phase 1: read-only scan returning technical-PDF candidates."""
        _require_auth(config, token)
        try:
            return await asyncio.to_thread(scan_folder, Path(request.source_path), config)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/imports/confirm")
    async def confirm_import(
        request: ImportConfirmRequest,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        """Phase 2: run the full import for the user-selected PDF."""
        _require_auth(config, token)
        try:
            result = await asyncio.to_thread(
                import_folder, Path(request.source_path), config, index, Path(request.technical_pdf)
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _import_payload(result)

    @app.post("/imports/upload")
    async def upload_import(
        files: Annotated[list[UploadFile], File(min_length=1, max_length=500)],
        folder: Annotated[str, Form(max_length=128)] = "upload",
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        """Stage browser drag-and-drop files, then scan for candidates.

        Each file's upload filename may carry a relative path
        (``REF001/sheet.pdf``) so folder structure survives the transfer.
        Source shares are untouched: bytes land in an isolated staging
        directory that is only ever read by the import pipeline.
        """
        _require_auth(config, token)
        safe_folder = re.sub(r"[^\w\-. ]", "_", folder.strip() or "upload").strip(" .") or "upload"
        staged = staging_root(config) / f"{uuid.uuid4().hex}_{safe_folder}"
        staged.mkdir(parents=True, exist_ok=True)
        try:
            for upload in files:
                relative = _safe_relative_path(upload.filename or "file")
                target = staged / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                size = 0
                with target.open("wb") as handle:
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        if size > config.max_file_size_bytes:
                            raise HTTPException(status_code=413, detail=f"File too large: {upload.filename}")
                        handle.write(chunk)
                await upload.close()
            result = await asyncio.to_thread(scan_folder, staged, config)
        except HTTPException:
            shutil.rmtree(staged, ignore_errors=True)
            raise
        except (PermissionError, ValueError) as exc:
            shutil.rmtree(staged, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"staged_path": str(staged), **result}

    @app.get("/imports/{import_id}")
    def read_import(
        import_id: str,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        result = get_import(index, import_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Import not found.")
        return result

    @app.patch("/imports/{import_id}")
    async def patch_import(
        import_id: str,
        request: ImportCorrectionRequest,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        """Apply a manual correction: re-validate, regenerate both reports, re-upload."""
        _require_auth(config, token)
        corrections = request.model_dump(exclude_none=True)
        try:
            return await asyncio.to_thread(correct_import, index, config, import_id, corrections)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Import not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/imports/{import_id}/retry-upload")
    async def retry_import_upload(
        import_id: str,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        try:
            return await asyncio.to_thread(retry_upload, index, config, import_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Import not found.") from exc

    return app


def _safe_relative_path(filename: str) -> Path:
    """Turn an upload filename into a staging-relative path (no escapes)."""
    parts = [part for part in Path(filename.replace("\\", "/")).parts if part not in ("", ".", "..", "/")]
    cleaned = [re.sub(r"[^\w\-+. ]", "_", part).strip(" .") or "file" for part in parts]
    return Path(*cleaned) if cleaned else Path("file")


def _require_auth(config: AppConfig, token: str | None) -> None:
    if config.auth_token and token != config.auth_token:
        raise HTTPException(status_code=401, detail="Authentication required.")


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
