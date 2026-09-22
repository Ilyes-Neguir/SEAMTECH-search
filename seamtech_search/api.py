from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .audit import actor_fingerprint, get_audit_logs, record_audit_event
from .config import AppConfig
from .extractors import extract_file
from .import_pipeline import (
    ImportResult,
    correct_import,
    get_import,
    import_folder,
    retry_upload,
    scan_folder,
    staging_root,
)
from .indexer import SearchIndex
from .jobs import (
    ImportCancelledError,
    cancel_job,
    clear_job_cancel,
    create_job,
    get_job,
    make_cancel_checker,
    recover_stale_jobs,
    update_job,
)
from .redis_store import RedisStore
from .retention import InsufficientStorageError, ensure_free_space, run_retention_cleanup
from .storage import S3StorageClient
from .worker import start_background_worker, stop_background_worker

logger = logging.getLogger("seamtech_search.api")

# Hard cap on the number of files accepted by /imports/upload. Enforced as an
# explicit check (not FastAPI's File(max_length=...)) so a dossier over the cap
# gets a clear, actionable 413 message instead of an opaque 422 validation
# error. Bump consciously: staging writes every file to disk before scanning.
MAX_UPLOAD_FILES = 500


class ImportRequest(BaseModel):
    source_path: str = Field(min_length=1, max_length=4_096)
    excel_file: str | None = Field(default=None, max_length=4_096)


class ImportScanRequest(BaseModel):
    source_path: str = Field(min_length=1, max_length=4_096)


class ImportConfirmRequest(BaseModel):
    source_path: str = Field(min_length=1, max_length=4_096)
    technical_pdf: str = Field(min_length=1, max_length=4_096)
    excel_file: str | None = Field(default=None, max_length=4_096)


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
    return asdict(result)


def _initialize_schema(index: SearchIndex, stage: str) -> None:
    """Initialize the schema and run migrations, failing loudly if they do not.

    Both call sites used to swallow failures -- the lifespan logged a warning and
    carried on, `create_app` had a bare `except Exception: pass` -- so the API
    could come up against a half-migrated schema. Every later query then failed
    in a way that pointed at the query rather than at the schema, and nothing in
    the logs said a migration had been skipped. Migration failures are now fatal
    at startup: a container that refuses to start is diagnosable, one that
    silently serves a wrong schema is not.

    Failures that are *expected* to be survivable degrade inside the migration
    instead of raising. See SearchIndex._ensure_unaccent_config, which falls back
    to the plain `simple` text-search configuration when the database role cannot
    CREATE EXTENSION, so a privilege gap costs accent-insensitive search rather
    than the whole service.
    """
    index.initialize()
    try:
        index.run_migrations()
    except Exception as exc:
        logger.exception("Schema migrations failed during %s; refusing to start", stage)
        raise RuntimeError(
            f"Schema migrations failed during {stage}: {exc}. Refusing to start against a "
            "half-migrated schema; the underlying database error is logged above."
        ) from exc


def create_app(config: AppConfig) -> FastAPI:
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if config.host not in local_hosts and config.auth_token and not config.behind_tls_proxy:
        raise ValueError("token authentication over non-local host requires behind_tls_proxy=true")

    index = SearchIndex(
        config.database_path,
        config.database_url,
        pool_min=config.pool_min,
        pool_max=config.pool_max,
        pool_timeout=config.pool_timeout,
        statement_timeout_ms=config.statement_timeout_ms,
    )

    redis_store = RedisStore(config=config)

    # One client for the whole app lifetime: it carries the versioning probe
    # cache that /health reports from, so a health-check loop does not turn into
    # a stream of S3 calls.
    storage_client: S3StorageClient | None = S3StorageClient(config=config)
    if not storage_client.is_configured():
        storage_client = None

    def _is_staged(source_path: str | Path) -> bool:
        """True when the import lives in the browser-upload staging area."""
        try:
            return staging_root(config).resolve() in Path(source_path).expanduser().resolve().parents
        except Exception:
            return False

    # Keep strong references to background tasks to prevent GC mid-flight (4.4)
    # Defined here so lifespan can use it before the later definition was moved
    background_tasks: set[asyncio.Task] = set()
    _retention_task: asyncio.Task | None = None

    async def _retention_loop() -> None:
        # Run once at startup after 60s, then every 24h (86400s)
        # Cadence documented in docs/VERIFICATION.md and README
        await asyncio.sleep(60)
        while True:
            try:
                res = await asyncio.to_thread(run_retention_cleanup, config, index)
                logger.info("Scheduled retention cleanup: %s", res)
            except Exception as exc:
                logger.warning("Scheduled retention cleanup failed: %s", exc)
            await asyncio.sleep(86400)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Initialize DB and run versioned migrations once at startup.
        # Health must not call initialize (read-only probe).
        _initialize_schema(index, "startup")
        recovered = recover_stale_jobs(index)
        if recovered > 0:
            logger.info("Recovered %d stale import jobs on startup", recovered)
        start_background_worker(config, index, redis_store)
        # Start retention scheduler (daily, preserves quarantine) - 4.3
        nonlocal _retention_task
        _retention_task = asyncio.create_task(_retention_loop())
        background_tasks.add(_retention_task)
        _retention_task.add_done_callback(background_tasks.discard)
        yield
        if _retention_task:
            _retention_task.cancel()
            try:
                await _retention_task
            except asyncio.CancelledError:
                pass
        stop_background_worker()
        index.close()

    # In production (auth_token set), disable public docs to avoid API surface disclosure
    docs_enabled = not bool(config.auth_token)
    app = FastAPI(
        title="SEAMTECH Search",
        version="0.4.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    # Eager init for TestClient usage that does not trigger lifespan, but health
    # itself remains read-only (does not call initialize).
    _initialize_schema(index, "app construction")

    request_timestamps: dict[str, list[float]] = defaultdict(list)
    rate_limit_lock = asyncio.Lock()

    metrics = {
        "search_requests": 0,
        "search_errors": 0,
        "last_search_seconds": 0.0,
        "slowest_search_seconds": 0.0,
        # Lot E (§17.2) : compteur hybride + suivi des recherches sans
        # résultat (matière première de l'amélioration du lexique, Phase 3).
        "recherche_requests": 0,
        "recherche_sans_resultat": 0,
    }

    # ---------------------------------------------------------------------------
    # Middleware: X-Request-ID & Sliding-Window Rate Limiter
    # ---------------------------------------------------------------------------

    @app.middleware("http")
    async def request_context_and_rate_limit(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id

        path = request.url.path
        # /docs and /openapi.json are NOT exempt — they are disabled in prod or require auth
        exempt_paths = {"/live", "/ready", "/health", "/metrics"}

        if path not in exempt_paths:
            client_ip = request.client.host if request.client else "127.0.0.1"
            auth_header = request.headers.get("X-SEAMTECH-TOKEN")
            client_key = f"{client_ip}:{auth_header or 'anon'}"

            is_limited, retry_after = False, 0
            if redis_store.is_configured():
                is_limited, retry_after = redis_store.check_rate_limit(client_key, config.rate_limit_per_minute)

            if not is_limited and not redis_store.is_configured():
                now = time.time()
                cutoff = now - 60.0
                async with rate_limit_lock:
                    # Sweep old entries to prevent unbounded growth (4.10)
                    for k in list(request_timestamps.keys()):
                        ts_list = request_timestamps[k]
                        filtered = [ts for ts in ts_list if ts > cutoff]
                        if filtered:
                            request_timestamps[k] = filtered
                        else:
                            del request_timestamps[k]

                    window = [ts for ts in request_timestamps.get(client_key, []) if ts > cutoff]
                    if len(window) >= config.rate_limit_per_minute:
                        oldest = window[0]
                        retry_after = max(1, int(60.0 - (now - oldest)) + 1)
                        request_timestamps[client_key] = window
                        is_limited = True
                    else:
                        window.append(now)
                        request_timestamps[client_key] = window

            if is_limited:
                actor = actor_fingerprint(auth_header, client_ip)
                record_audit_event(
                    index,
                    action="rate_limit_exceeded",
                    actor=actor,
                    resource=path,
                    status="429",
                    details={"request_id": request_id, "retry_after": retry_after},
                )
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too Many Requests. Rate limit exceeded."},
                    headers={"Retry-After": str(retry_after), "X-Request-ID": request_id},
                )

        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    # ---------------------------------------------------------------------------
    # Probes
    # ---------------------------------------------------------------------------

    @app.get("/live")
    def live_probe() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/ready")
    def ready_probe() -> dict[str, str]:
        try:
            with index.connect() as conn:
                if index.is_postgres:
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT 1")
                else:
                    conn.execute("SELECT 1")
            return {"status": "ready"}
        except Exception as exc:
            logger.error("Readiness check failed: %s", exc)
            raise HTTPException(status_code=503, detail="Database not ready") from exc

    # ---------------------------------------------------------------------------
    # Core API Endpoints
    # ---------------------------------------------------------------------------

    @app.get("/")
    def root(token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None) -> dict[str, str]:
        _require_auth(config, token)
        return {"service": "seamtech-search-api", "health": "/health", "docs": "/docs"}

    @app.get("/health")
    def health(token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None) -> dict[str, object]:
        _require_auth(config, token)
        stats = index.stats()
        health_details = index.health_details()
        disk_path = config.database_path.parent if not index.is_postgres else Path.cwd()
        disk_usage = shutil.disk_usage(disk_path)
        versioning = (
            storage_client.versioning_status()
            if storage_client is not None
            else {"versioning_available": None, "versioning_detail": "object storage is not configured"}
        )
        deadletter_count = 0
        try:
            if redis_store.is_configured():
                deadletter_count = redis_store.get_deadletter_count("imports")
        except Exception:
            deadletter_count = 0

        return {
            "status": "ok",
            **health_details,
            "disk_free_bytes": disk_usage.free,
            "disk_total_bytes": disk_usage.total,
            "documents": stats.total_documents,
            "files": stats.files,
            "folders": stats.folders,
            "last_scan": index.latest_scan(),
            "redis_connected": redis_store.ping() if redis_store.is_configured() else None,
            "storage_backend": config.storage_backend,
            "s3_configured": bool(config.s3_endpoint_url or config.s3_access_key),
            "upload_dead_letters": deadletter_count,
            # Read-only probe (never puts versioning): False means the endpoint
            # cannot version (Cloudflare R2), None means "could not find out".
            **versioning,
        }

    @app.get("/search")
    def search(
        request: Request,
        q: str = Query(..., min_length=1, max_length=500),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0, le=1_000_000),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        actor = actor_fingerprint(token, request.client.host if request.client else None)
        try:
            started_at = time.perf_counter()
            raw_results = index.search(q, limit=limit + 1, offset=offset)
            results = raw_results[:limit]
            has_more = len(raw_results) > limit
            elapsed = time.perf_counter() - started_at
            metrics["search_requests"] += 1
            metrics["last_search_seconds"] = elapsed
            metrics["slowest_search_seconds"] = max(float(metrics["slowest_search_seconds"]), elapsed)
            record_audit_event(
                index,
                action="search",
                actor=actor,
                resource=q,
                status="success",
                details={"limit": limit, "offset": offset, "count": len(results)},
            )
        except (ValueError, RuntimeError) as exc:
            metrics["search_errors"] += 1
            record_audit_event(
                index, action="search", actor=actor, resource=q, status="error", details={"error": str(exc)}
            )
            raise HTTPException(status_code=400, detail="Invalid search query.") from exc
        except Exception as exc:
            metrics["search_errors"] += 1
            record_audit_event(
                index, action="search", actor=actor, resource=q, status="error", details={"error": str(exc)}
            )
            raise HTTPException(status_code=500, detail="Search service failure.") from exc
        return {
            "query": q,
            "count": len(results),
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
            "results": results,
        }

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
        request: Request,
        path: str = Query(..., min_length=1, max_length=4_096),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> Response:
        _require_auth(config, token)
        actor = actor_fingerprint(token, request.client.host if request.client else None)
        target = _validated_path(path, config)

        # Try to find object storage key for this path
        object_key = None
        try:
            with index.connect() as conn:
                if index.is_postgres:
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT object_key FROM documents WHERE path_key = %s", (str(target.resolve()),))
                        row = cursor.fetchone()
                        if row and row[0]:
                            object_key = row[0]
                else:
                    row = conn.execute("SELECT object_key FROM documents WHERE path_key = ?", (str(target.resolve()),)).fetchone()
                    if row and row["object_key"]:
                        object_key = row["object_key"]
        except Exception as exc:
            # Falling back to the local file is fine, but a DB error here is a
            # signal of a broken schema/index and must not vanish silently.
            logger.warning("Could not look up object_key for %s: %s", target, exc)

        if storage_client is not None and object_key:
            try:
                url = storage_client.get_presigned_url(object_key, expires_in=900)
                record_audit_event(
                    index, action="open", actor=actor, resource=str(target), status="302", details={"object_key": object_key}
                )
                return RedirectResponse(url=url, status_code=302)
            except Exception as exc:
                logger.warning("Failed presigned URL for open %s: %s", target, exc)

        # Fallback: serve file directly if it exists locally
        if target.exists() and target.is_file():
            record_audit_event(index, action="open", actor=actor, resource=str(target), status="200")
            return FileResponse(path=target, filename=target.name)

        # For directories, return listing instead of trying OS open
        if target.exists() and target.is_dir():
            record_audit_event(index, action="open", actor=actor, resource=str(target), status="200")
            return JSONResponse({"opened": str(target), "is_dir": True, "note": "Directory listing via /preview"})

        record_audit_event(index, action="open", actor=actor, resource=str(target), status="404")
        raise HTTPException(status_code=404, detail="Path not found.")

    # ---------------------------------------------------------------------------
    # Import Endpoints (Async jobs by default, sync with ?wait=true)
    # ---------------------------------------------------------------------------

    def _execute_import_background(
        job_id: str,
        source_path: Path,
        selected_pdf: Path | None = None,
        selected_excel: Path | None = None,
    ) -> None:
        def progress_cb(stage: str, percent: int) -> None:
            update_job(index, job_id, status="running", progress=percent, stage=stage)

        cancel_check = make_cancel_checker(job_id)

        try:
            update_job(index, job_id, status="running", progress=5, stage="starting")
            result = import_folder(
                source=source_path,
                config=config,
                index=index,
                selected_pdf=selected_pdf,
                import_id=job_id,
                progress_callback=progress_cb,
                cancel_check=cancel_check,
                selected_excel=selected_excel,
            )
            if cancel_check():
                raise ImportCancelledError("Job was cancelled by user")
            final_payload = _import_payload(result)
            final_status = "completed" if result.status == "completed" else result.status
            update_job(
                index,
                job_id,
                status=final_status,
                progress=100,
                stage="done",
                result=final_payload,
            )
        except ImportCancelledError:
            update_job(index, job_id, status="cancelled", stage="cancelled", error="Job was cancelled by user")
        except InsufficientStorageError as exc:
            update_job(index, job_id, status="failed", stage="failed", error=str(exc))
        except Exception as exc:
            logger.exception("Import job %s failed: %s", job_id, exc)
            update_job(index, job_id, status="failed", stage="failed", error=str(exc))
        finally:
            clear_job_cancel(job_id)

    @app.post("/imports")
    async def create_import(
        request_body: ImportRequest,
        request: Request,
        wait: bool = Query(False),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> Response:
        _require_auth(config, token)
        actor = actor_fingerprint(token, request.client.host if request.client else None)
        source = Path(request_body.source_path)
        excel_path = Path(request_body.excel_file) if request_body.excel_file else None

        try:
            ensure_free_space(config.database_path.parent, config.min_free_bytes)
        except InsufficientStorageError as exc:
            record_audit_event(
                index,
                action="import_create",
                actor=actor,
                resource=str(source),
                status="507",
                details={"error": str(exc)},
            )
            raise HTTPException(status_code=507, detail=str(exc)) from exc

        job_id = uuid.uuid4().hex

        if wait:
            try:
                result = await asyncio.to_thread(
                    import_folder, source, config, index, None, job_id, None, None, excel_path
                )
                payload = _import_payload(result)
                record_audit_event(
                    index,
                    action="import_create",
                    actor=actor,
                    resource=job_id,
                    status="success",
                    details={"sync": True},
                )
                return JSONResponse(status_code=200, content=payload)
            except InsufficientStorageError as exc:
                record_audit_event(index, action="import_create", actor=actor, resource=job_id, status="507")
                raise HTTPException(status_code=507, detail=str(exc)) from exc
            except PermissionError as exc:
                record_audit_event(index, action="import_create", actor=actor, resource=job_id, status="403")
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except ValueError as exc:
                record_audit_event(index, action="import_create", actor=actor, resource=job_id, status="400")
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            resolved = Path(request_body.source_path).expanduser().resolve()
            if not resolved.exists() or not resolved.is_dir():
                raise ValueError("Import path must be an existing directory")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        create_job(index, job_id, request_body.source_path, status="pending", stage="queued")
        if redis_store.is_configured() and redis_store.ping():
            task_payload = {
                "job_id": job_id,
                "source_path": request_body.source_path,
                "selected_pdf": None,
                "selected_excel": request_body.excel_file,
            }
            redis_store.set_job(
                job_id,
                {"id": job_id, "status": "pending", "progress": 0, "stage": "queued", "source_path": request_body.source_path},
            )
            redis_store.enqueue_task("imports", task_payload)
        else:
            task = asyncio.create_task(asyncio.to_thread(_execute_import_background, job_id, source, None, excel_path))
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)

        record_audit_event(
            index,
            action="import_create",
            actor=actor,
            resource=job_id,
            status="accepted",
            details={"async": True},
        )

        return JSONResponse(
            status_code=202,
            content={
                "job_id": job_id,
                "id": job_id,
                "import_id": job_id,
                "status": "pending",
                "progress": 0,
                "stage": "queued",
                "source_path": request_body.source_path,
            },
        )

    @app.post("/imports/scan")
    async def scan_import(
        request_body: ImportScanRequest,
        request: Request,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        actor = actor_fingerprint(token, request.client.host if request.client else None)
        try:
            res = await asyncio.to_thread(scan_folder, Path(request_body.source_path), config)
            record_audit_event(
                index, action="import_scan", actor=actor, resource=request_body.source_path, status="success"
            )
            return res
        except PermissionError as exc:
            record_audit_event(
                index, action="import_scan", actor=actor, resource=request_body.source_path, status="403"
            )
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            record_audit_event(
                index, action="import_scan", actor=actor, resource=request_body.source_path, status="400"
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/imports/confirm")
    async def confirm_import(
        request_body: ImportConfirmRequest,
        request: Request,
        wait: bool = Query(True),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> Response:
        _require_auth(config, token)
        actor = actor_fingerprint(token, request.client.host if request.client else None)
        source = Path(request_body.source_path)
        technical_pdf = Path(request_body.technical_pdf)
        excel_path = Path(request_body.excel_file) if request_body.excel_file else None

        try:
            ensure_free_space(config.database_path.parent, config.min_free_bytes)
        except InsufficientStorageError as exc:
            record_audit_event(index, action="import_confirm", actor=actor, resource=str(source), status="507")
            raise HTTPException(status_code=507, detail=str(exc)) from exc

        job_id = uuid.uuid4().hex

        if wait:
            try:
                result = await asyncio.to_thread(
                    import_folder, source, config, index, technical_pdf, job_id, None, None, excel_path
                )
                payload = _import_payload(result)
                record_audit_event(index, action="import_confirm", actor=actor, resource=job_id, status="success")
                return JSONResponse(status_code=200, content=payload)
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        create_job(index, job_id, request_body.source_path, status="pending", stage="queued")
        if redis_store.is_configured() and redis_store.ping():
            task_payload = {
                "job_id": job_id,
                "source_path": request_body.source_path,
                "selected_pdf": str(technical_pdf),
                "selected_excel": str(excel_path) if excel_path else None,
            }
            redis_store.set_job(
                job_id,
                {"id": job_id, "status": "pending", "progress": 0, "stage": "queued", "source_path": request_body.source_path},
            )
            redis_store.enqueue_task("imports", task_payload)
        else:
            task = asyncio.create_task(
                asyncio.to_thread(_execute_import_background, job_id, source, technical_pdf, excel_path)
            )
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)
        record_audit_event(
            index,
            action="import_confirm",
            actor=actor,
            resource=job_id,
            status="accepted",
            details={"async": True},
        )
        return JSONResponse(
            status_code=202,
            content={
                "job_id": job_id,
                "id": job_id,
                "import_id": job_id,
                "status": "pending",
                "progress": 0,
                "stage": "queued",
                "source_path": request_body.source_path,
            },
        )

    @app.post("/imports/upload")
    async def upload_import(
        request: Request,
        files: Annotated[list[UploadFile], File(min_length=1)],
        folder: Annotated[str, Form(max_length=128)] = "upload",
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        actor = actor_fingerprint(token, request.client.host if request.client else None)
        if len(files) > MAX_UPLOAD_FILES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Too many files: {len(files)} uploaded, maximum is {MAX_UPLOAD_FILES} per upload. "
                    "Split the folder into several batches and upload them separately."
                ),
            )

        try:
            ensure_free_space(config.database_path.parent, config.min_free_bytes)
        except InsufficientStorageError as exc:
            record_audit_event(index, action="import_upload", actor=actor, status="507")
            raise HTTPException(status_code=507, detail=str(exc)) from exc

        safe_folder = re.sub(r"[^\w\-. ]", "_", folder.strip() or "upload").strip(" .") or "upload"
        staged = staging_root(config) / f"{uuid.uuid4().hex}_{safe_folder}"
        staged.mkdir(parents=True, exist_ok=True)
        total_size = 0
        max_aggregate = config.max_file_size_bytes * 10  # aggregate cap: 10x single file
        try:
            for upload in files:
                relative = _safe_relative_path(upload.filename or "file")
                target = staged / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                size = 0
                with target.open("wb") as handle:
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        total_size += len(chunk)
                        if size > config.max_file_size_bytes:
                            raise HTTPException(status_code=413, detail=f"File too large: {upload.filename}")
                        if total_size > max_aggregate:
                            raise HTTPException(status_code=413, detail="Aggregate upload too large")
                        # Re-check free space while writing (4.10)
                        try:
                            ensure_free_space(config.database_path.parent, config.min_free_bytes)
                        except InsufficientStorageError as exc:
                            raise HTTPException(status_code=507, detail=str(exc)) from exc
                        handle.write(chunk)
                await upload.close()
            result = await asyncio.to_thread(scan_folder, staged, config)
            record_audit_event(index, action="import_upload", actor=actor, resource=str(staged), status="success")
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
        # Check DB first for completed/corrected records to avoid stale Redis cache shadowing (4.10)
        record = get_import(index, import_id)
        job = get_job(index, import_id)

        if redis_store.is_configured():
            cached = redis_store.get_job(import_id)
            if cached:
                # For running/pending, prefer Redis fast-path
                if cached.get("status") in ("running", "pending"):
                    res_data = cached.get("result") or {}
                    return {**cached, **res_data, "job_id": cached.get("id", import_id)}
                # For completed/cancelled, if DB has newer corrected data, prefer DB
                if cached.get("status") in ("completed", "cancelled") and record is None:
                    res_data = cached.get("result") or {}
                    return {**cached, **res_data, "job_id": cached.get("id", import_id)}

        if job is not None:
            if job["status"] == "cancelled":
                return job
            if record is not None:
                return {**job, **record, "job_id": job["id"], "status": record.get("status", job["status"])}
            result_data = job.get("result") or {}
            combined = {**job, **result_data}
            combined["job_id"] = job["id"]
            combined["status"] = job["status"]
            combined["progress"] = job["progress"]
            combined["stage"] = job["stage"]
            if job.get("error"):
                combined["error"] = job["error"]
            return combined
        if record is not None:
            return record

        raise HTTPException(status_code=404, detail="Import not found.")

    @app.post("/imports/{import_id}/cancel")
    def cancel_import_job(
        import_id: str,
        request: Request,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        actor = actor_fingerprint(token, request.client.host if request.client else None)
        updated = cancel_job(index, import_id)
        record_audit_event(
            index,
            action="import_cancel",
            actor=actor,
            resource=import_id,
            status="success" if updated else "404",
        )
        if updated is None:
            rec = get_import(index, import_id)
            if rec is None:
                raise HTTPException(status_code=404, detail="Import not found.")
            return {"job_id": import_id, "status": "cancelled"}
        return updated

    @app.patch("/imports/{import_id}")
    async def patch_import(
        import_id: str,
        request_body: ImportCorrectionRequest,
        request: Request,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        actor = actor_fingerprint(token, request.client.host if request.client else None)
        corrections = request_body.model_dump(exclude_none=True)
        try:
            res = await asyncio.to_thread(correct_import, index, config, import_id, corrections)
            # Invalidate Redis cache so corrected DB record is not shadowed (4.10)
            if redis_store.is_configured():
                try:
                    redis_store.update_job(import_id, {"result": res, "status": res.get("status", "completed")})
                except Exception as exc:
                    # DB record is already corrected; a failed cache refresh
                    # leaves a stale cached copy visible until it expires, so
                    # make the failure visible rather than swallowing it.
                    logger.warning("Could not refresh Redis cache for import %s after correction: %s", import_id, exc)
            record_audit_event(index, action="import_correct", actor=actor, resource=import_id, status="success")
            return res
        except KeyError as exc:
            record_audit_event(index, action="import_correct", actor=actor, resource=import_id, status="404")
            raise HTTPException(status_code=404, detail="Import not found.") from exc
        except ValueError as exc:
            record_audit_event(index, action="import_correct", actor=actor, resource=import_id, status="400")
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/imports/{import_id}/retry-upload")
    async def retry_import_upload(
        import_id: str,
        request: Request,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        actor = actor_fingerprint(token, request.client.host if request.client else None)
        try:
            res = await asyncio.to_thread(retry_upload, index, config, import_id)
            if redis_store.is_configured():
                try:
                    redis_store.update_job(import_id, {"result": res, "status": res.get("status", "completed")})
                except Exception as exc:
                    # Same stale-cache trade-off as import_correct: the DB
                    # record is already updated, but a failed refresh means the
                    # old cached copy can shadow it until it expires.
                    logger.warning("Could not refresh Redis cache for import %s after retry-upload: %s", import_id, exc)
            record_audit_event(index, action="import_retry_upload", actor=actor, resource=import_id, status="success")
            return res
        except KeyError as exc:
            record_audit_event(index, action="import_retry_upload", actor=actor, resource=import_id, status="404")
            raise HTTPException(status_code=404, detail="Import not found.") from exc

    @app.get("/imports/{import_id}/artifacts/{artifact}")
    def get_import_artifact(
        import_id: str,
        artifact: str,
        request: Request,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> Response:
        _require_auth(config, token)
        actor = actor_fingerprint(token, request.client.host if request.client else None)

        allowed = {"report_pdf", "report_docx", "source_pdf", "source_excel"}
        if artifact not in allowed:
            raise HTTPException(status_code=400, detail=f"Unknown artifact. Allowed: {', '.join(sorted(allowed))}")

        payload = get_import(index, import_id)
        if payload is None:
            record_audit_event(index, action="artifact_download", actor=actor, resource=f"{import_id}/{artifact}", status="404")
            raise HTTPException(status_code=404, detail="Import not found.")

        # Resolve file path and object key from payload
        file_path: Path | None = None
        object_key: str | None = None
        filename: str = ""
        media_type: str = "application/octet-stream"

        if artifact == "report_pdf":
            file_path = Path(payload["report_path"]) if payload.get("report_path") else None
            filename = "technical-report.pdf"
            media_type = "application/pdf"
            # Try to find object_key from artifacts list
            for art in payload.get("artifacts", []):
                if art.get("name") == "technical-report.pdf" and art.get("key"):
                    object_key = art["key"]
                    break
        elif artifact == "report_docx":
            file_path = Path(payload["report_docx_path"]) if payload.get("report_docx_path") else None
            filename = "technical-report.docx"
            media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            for art in payload.get("artifacts", []):
                if art.get("name") == "technical-report.docx" and art.get("key"):
                    object_key = art["key"]
                    break
        elif artifact == "source_pdf":
            file_path = Path(payload["technical_pdf"]) if payload.get("technical_pdf") else None
            filename = Path(file_path).name if file_path else "source.pdf"
            media_type = "application/pdf"
            # Find in files list
            for f in payload.get("files", []):
                if f.get("path") == payload.get("technical_pdf") and f.get("object_key"):
                    object_key = f["object_key"]
                    break
        elif artifact == "source_excel":
            file_path = Path(payload["excel_file"]) if payload.get("excel_file") else None
            filename = Path(file_path).name if file_path else "source.xlsx"
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            for f in payload.get("files", []):
                if f.get("path") == payload.get("excel_file") and f.get("object_key"):
                    object_key = f["object_key"]
                    break

        # If S3 configured and we have object key, redirect to presigned URL (expiry <=15 min)
        if storage_client is not None and object_key:
            try:
                url = storage_client.get_presigned_url(object_key, expires_in=900)
                record_audit_event(
                    index,
                    action="artifact_download",
                    actor=actor,
                    resource=f"{import_id}/{artifact}",
                    status="302",
                    details={"object_key": object_key, "filename": filename},
                )
                return RedirectResponse(url=url, status_code=302)
            except Exception as exc:
                logger.warning("Failed to generate presigned URL for %s: %s", object_key, exc)
                # Fall back to local file if available

        # Fallback: serve from local disk (cache) or download from S3 to cache
        if file_path and file_path.exists():
            record_audit_event(
                index,
                action="artifact_download",
                actor=actor,
                resource=f"{import_id}/{artifact}",
                status="200",
                details={"path": str(file_path), "filename": filename},
            )
            return FileResponse(path=file_path, filename=filename, media_type=media_type)

        # If local cache cold but we have object_key, try to download from S3 to cache dir
        if storage_client is not None and object_key and file_path:
            try:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                storage_client.download_file(object_key, file_path)
                if file_path.exists():
                    record_audit_event(
                        index,
                        action="artifact_download",
                        actor=actor,
                        resource=f"{import_id}/{artifact}",
                        status="200",
                        details={"object_key": object_key, "filename": filename, "cache": "miss_downloaded"},
                    )
                    return FileResponse(path=file_path, filename=filename, media_type=media_type)
            except Exception as exc:
                logger.warning("Failed to download %s from storage: %s", object_key, exc)

        record_audit_event(index, action="artifact_download", actor=actor, resource=f"{import_id}/{artifact}", status="404")
        raise HTTPException(status_code=404, detail="Artifact not found. It may not have been generated or uploaded yet.")

    # ---------------------------------------------------------------------------
    # Maintenance & Audit Endpoints
    # ---------------------------------------------------------------------------

    @app.post("/maintenance/replay-deadletters")
    def replay_deadletters(
        request: Request,
        limit: int = Query(10, ge=1, le=100),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        actor = actor_fingerprint(token, request.client.host if request.client else None)
        if not redis_store.is_configured():
            raise HTTPException(status_code=503, detail="Redis not configured")
        count = redis_store.replay_deadletters("imports", limit=limit)
        record_audit_event(
            index, action="replay_deadletters", actor=actor, status="success", details={"replayed": count}
        )
        return {"status": "ok", "replayed": count}

    @app.get("/maintenance/deadletters")
    def list_deadletters(
        request: Request,
        limit: int = Query(50, ge=1, le=200),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        if not redis_store.is_configured():
            raise HTTPException(status_code=503, detail="Redis not configured")
        dead = redis_store.get_deadletters("imports", limit=limit)
        return {"count": len(dead), "results": dead}

    @app.post("/maintenance/cleanup")
    async def maintenance_cleanup(
        request: Request,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        actor = actor_fingerprint(token, request.client.host if request.client else None)
        res = await asyncio.to_thread(run_retention_cleanup, config, index)
        record_audit_event(index, action="maintenance_cleanup", actor=actor, status="success", details=res)
        return {"status": "ok", "cleanup": res}

    @app.get("/audit")
    def list_audit_events(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0, le=1_000_000),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        logs = get_audit_logs(index, limit=limit, offset=offset)
        return {
            "count": len(logs),
            "offset": offset,
            "limit": limit,
            "results": logs,
        }

    # Lot B.2 (§17.11) : traçabilité des champs et registre de gabarits —
    # lecture et publication uniquement, aucune écriture de fiche par ces routes.
    from seamtech_search.fiches.routes import enregistrer_routes_fiches

    enregistrer_routes_fiches(app, index, config, _require_auth)

    # Lot E (§17.2, §17.5) : recherche hybride des fiches — GET /recherche et
    # GET /recherche/suggestions. L'existant (/search fichiers) n'est pas touché.
    # Lot F : l'encodeur e5 ONNX est chargé s'il est sur le disque (poids
    # téléchargés explicitement — jamais au runtime) ; sinon encode_requete
    # reste None et la branche vectorielle demeure dormante, sans erreur.
    from seamtech_search.recherche import enregistrer_routes_recherche

    encodeur_ml = _charger_encodeur_ml(config)
    enregistrer_routes_recherche(
        app,
        index,
        config,
        _require_auth,
        metriques=metrics,
        encode_requete=encodeur_ml.vecteur_requete if encodeur_ml is not None else None,
    )

    # Lot I (§11, Phase 4) : assistant sourcé — POST /assistant (extractif,
    # sans LLM : réponses construites depuis la base, citations obligatoires).
    from seamtech_search.assistant import enregistrer_routes_assistant

    enregistrer_routes_assistant(app, index, config, _require_auth, metriques=metrics)

    from seamtech_search.ml.routes import enregistrer_routes_ml

    enregistrer_routes_ml(
        app,
        index,
        config,
        _require_auth,
        modeles_dir=_dossier_modeles_ml(config),
        racine_verite=Path(__file__).resolve().parents[1],
    )

    return app


def _dossier_modeles_ml(config: AppConfig) -> Path:
    """Dossier des poids/modèles : <data>/modeles, surclassable par env."""
    import os

    surclasse = os.environ.get("SEAMTECH_ML_MODELE_DIR")
    if surclasse:
        return Path(surclasse)
    return Path(config.database_path).resolve().parent / "modeles"


def _charger_encodeur_ml(config: AppConfig) -> Any:
    from seamtech_search.ml.encodeur import charger_encodeur

    encodeur = charger_encodeur(_dossier_modeles_ml(config))
    if encodeur is not None:
        logger.info("Encodeur ML actif : %s (source vecteurs activée).", encodeur.nom)
    return encodeur


def _safe_relative_path(filename: str) -> Path:
    parts = [part for part in Path(filename.replace("\\", "/")).parts if part not in ("", ".", "..", "/")]
    cleaned = [re.sub(r"[^\w\-+. ]", "_", part).strip(" .") or "file" for part in parts]
    return Path(*cleaned) if cleaned else Path("file")


def _require_auth(config: AppConfig, token: str | None) -> None:
    if not config.auth_token:
        return
    if token is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    import secrets

    # Constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(token.encode("utf-8"), config.auth_token.encode("utf-8")):
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
