from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import shutil
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, Iterator
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import __version__
from .audit import actor_fingerprint, get_audit_logs, record_audit_event
from .comptes.comptes import ROLE_ADMIN, ROLE_APPROBATEUR, ROLE_UTILISATEUR
from .config import AppConfig
from .etat_exploitation import etat_sauvegarde, s3_credential_kind
from .extractors import extract_file
from .fichiers_securises import (
    _copie_flux_verifiee,
    _ouvrir_artefact_confine,
    _ReponseFichierEpingle,
    _ReponseFluxFerme,
)
from .import_pipeline import (
    ImportMutationEnCours,
    ImportMutationIncertitude,
    ImportResult,
    PublicationIncertaine,
    RapportsEnRegeneration,
    correct_import,
    get_import,
    import_folder,
    quarantine_root,
    resoudre_chemin_confine,
    retry_upload,
    scan_folder,
    staging_root,
)
from .indexer import SearchIndex
from .jobs import (
    ImportCancelledError,
    cancel_job,
    clear_job_cancel,
    compter_jobs_par_statut,
    create_job,
    get_job,
    jobs_actifs,
    make_cancel_checker,
    recover_stale_jobs,
    update_job,
)
from .redis_store import RedisStore
from .retention import InsufficientStorageError, ensure_free_space, run_retention_cleanup
from .securite_requetes import (
    exiger_acteur_humain,
    exiger_administrateur_requete,
    exiger_role_si_humain,
    resoudre_acteur_audit,
)
from .storage import S3StorageClient
from .worker import file_durable_disponible, start_background_worker, stop_background_worker

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


def fonctionnalites_optionnelles_actives() -> bool:
    """Assistant et routes ML visibles par défaut; désactivables au déploiement."""
    return os.environ.get("SEAMTECH_OPTIONAL_FEATURES_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}



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

    def _exiger_role_si_humain_depuis_requete(
        request: Request,
        token: str | None,
        roles: tuple[str, ...],
    ) -> None:
        """Applique la matrice Lot 1 dès qu'un humain est présenté.

        Les routes d'import historiques restent appelables par l'outillage de
        service de confiance ; un compte ``lecture`` présenté par le proxy ne
        peut en revanche plus traverser une mutation grâce au jeton de service.
        """
        exiger_role_si_humain(
            index,
            config,
            roles,
            token=token,
            entete_utilisateur=request.headers.get("X-SEAMTECH-UTILISATEUR"),
            entete_role=request.headers.get("X-SEAMTECH-ROLE"),
            entete_session=request.headers.get("X-SEAMTECH-SESSION"),
            entete_jeton=request.headers.get("X-SEAMTECH-SESSION-JETON"),
            client_host=request.client.host if request.client else None,
        )

    # One client for the whole app lifetime: it carries the versioning probe
    # cache that /health reports from, so a health-check loop does not turn into
    # a stream of S3 calls.
    storage_client: S3StorageClient | None = S3StorageClient(config=config)
    if not storage_client.is_configured():
        storage_client = None
    elif not storage_client.credentials_presentes():
        # Configuration INCOMPLÈTE, dite explicitement : sans cette alerte,
        # l'absence d'identité ressemblerait plus tard à une panne du stockage
        # (« Access Denied » au milieu d'un import). Aucun repli sur les
        # identifiants administrateur n'existe : /health renvoie « absent ».
        logger.error(
            "Stockage objet configuré (endpoint=%s bucket=%s) mais identifiants applicatifs "
            "absents : renseigner SEAMTECH_S3_ACCESS_KEY et SEAMTECH_S3_SECRET_KEY "
            "(identité dédiée créée par scripts/provisionner_stockage.sh). "
            "Aucun repli sur l'administrateur MinIO n'est effectué.",
            config.s3_endpoint_url,
            config.s3_bucket,
        )

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
        # Registre des gabarits : semer les gabarits embarqués absents —
        # sans eux, tout dépôt est refusé « gabarit inconnu ». La
        # synchronisation est ADDITIVE : elle couvre la base neuve comme la
        # mise à jour d'une base existante (nouvelle famille livrée par le
        # code), sans jamais réécrire une ligne déjà publiée ou
        # personnalisée (RG11). Registre PostgreSQL-only, comme le dépôt de
        # dossiers lui-même.
        if index.is_postgres:
            try:
                from seamtech_search.fiches.gabarits import synchroniser_gabarits_embarques

                ajoutes = synchroniser_gabarits_embarques(index)
                if ajoutes:
                    logger.info("Gabarits embarqués ajoutés au registre : %s.", ", ".join(ajoutes))
            except Exception:
                logger.exception(
                    "Échec de la synchronisation des gabarits — conséquence : les dépôts "
                    "seront refusés « gabarit inconnu » pour les familles absentes du registre."
                )
        recovered = recover_stale_jobs(index, redis_store=redis_store)
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
        version=__version__,
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
    # Lot 0b — acteur, audit HTTP et confinement des artefacts
    # ---------------------------------------------------------------------------

    def _ressource_audit(chemin: str) -> tuple[str, str]:
        """Type/identifiant d'objet pour le journal, sans query string."""
        segments = [segment for segment in chemin.split("/") if segment]
        if len(segments) >= 3 and segments[0] == "pieces":
            return "piece", segments[1]
        if len(segments) >= 4 and segments[0] == "imports" and segments[2] == "artifacts":
            return "import_artifact", f"{segments[1]}/{segments[3]}"
        if len(segments) >= 2 and segments[0] == "fiches":
            return "fiche", segments[1]
        if len(segments) >= 2 and segments[0] == "lots":
            return "lot", segments[1]
        if len(segments) >= 3 and segments[0] == "gabarits" and segments[1] == "brouillons":
            return "gabarit_brouillon", segments[2]
        if len(segments) >= 3 and segments[0] == "auth" and segments[1] == "utilisateurs":
            return "utilisateur", segments[2]
        return "route", chemin

    def _requete_a_auditer(methode: str, chemin: str, code_statut: int) -> bool:
        """Événements HTTP versés au journal canonique.

        Toutes les mutations, tous les refus et les lectures sensibles
        (fichiers, audit, maintenance, administration, journal de recherche)
        sont tracés. Les lectures ordinaires de recherche restent couvertes
        par leurs journaux métier dédiés, sans doubler chaque frappe dans
        ``audit_logs``. Ni corps ni query string ne sont persistés ici.
        """
        if chemin in {"/live", "/ready", "/health"}:
            return False
        if methode.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            return True
        if code_statut >= 400:
            return True
        if chemin in {"/audit", "/metrics", "/recherche/journal", "/auth/utilisateurs"}:
            return True
        if chemin.startswith("/maintenance/"):
            return True
        if re.fullmatch(r"/pieces/\d+/(apercu|telecharger)", chemin):
            return True
        if re.fullmatch(r"/imports/[^/]+/artifacts/[^/]+", chemin):
            return True
        return False

    def _chemin_artefact_autorise(chemin: Path) -> bool:
        """Un chemin lu depuis un payload d'import reste confiné.

        Le payload est produit par le serveur, mais le Lot 0b ne le traite pas
        comme une autorisation : seules les racines d'archive, le staging et
        le dossier des rapports générés peuvent être servis ou alimentés en
        cache depuis S3.
        """
        try:
            # Python ≥ 3.13 ne signale plus une boucle de liens symboliques :
            # la vérification explicite garde le refus (ValueError attrapée plus bas).
            cible = resoudre_chemin_confine(chemin)
            racines = [Path(root).expanduser().resolve() for root in config.root_paths]
            racines.append(staging_root(config).expanduser().resolve())
            racines.append(quarantine_root(config).expanduser().resolve())
            racines.append((Path(config.database_path).expanduser().resolve().parent / "reports"))
            return any(cible == racine or racine in cible.parents for racine in racines)
        except (OSError, RuntimeError, TypeError, ValueError):
            return False

    def _enregistrer_audit_http(
        *,
        methode: str,
        chemin: str,
        code_statut: int,
        request_id: str,
        entetes: dict[str, str],
        client_host: str | None,
        user_agent: str | None,
    ) -> None:
        acteur = resoudre_acteur_audit(index, config, entetes, client_host)
        resource_type, resource_id = _ressource_audit(chemin)
        record_audit_event(
            index,
            action="http_request",
            actor=acteur.libelle_audit,
            resource=chemin,
            status=str(code_statut),
            details={"method": methode, "path": chemin},
            request_id=request_id,
            resource_type=resource_type,
            resource_id=resource_id,
            code_statut=code_statut,
            adresse_ip=client_host,
            user_agent=user_agent,
            **acteur.contexte_audit(),
        )

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
                    headers={
                        "Retry-After": str(retry_after),
                        "X-Request-ID": request_id,
                        "Cache-Control": "no-store, no-cache, must-revalidate, private",
                    },
                )

        # La porte de service est vérifiée avant le routage/validation : un
        # jeton absent ou faux ne doit révéler ni le schéma d'entrée, ni
        # déclencher le parsing d'un corps (multipart compris). Les gardes
        # par route restent en place en défense en profondeur.
        if config.auth_token and path not in {"/live", "/ready"}:
            jeton_fourni = request.headers.get("X-SEAMTECH-TOKEN")
            if jeton_fourni is None or not secrets.compare_digest(
                jeton_fourni.encode("utf-8"), config.auth_token.encode("utf-8")
            ):
                reponse_refus = JSONResponse(
                    status_code=401,
                    content={"detail": "Authentication required."},
                    headers={
                        "X-Request-ID": request_id,
                        "Cache-Control": "no-store, no-cache, must-revalidate, private",
                    },
                )
                if _requete_a_auditer(request.method, path, reponse_refus.status_code):
                    await asyncio.to_thread(
                        _enregistrer_audit_http,
                        methode=request.method,
                        chemin=path,
                        code_statut=reponse_refus.status_code,
                        request_id=request_id,
                        entetes=dict(request.headers),
                        client_host=request.client.host if request.client else None,
                        user_agent=request.headers.get("User-Agent"),
                    )
                return reponse_refus

        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if path not in exempt_paths:
            # Lot 0a : aucune réponse authentifiée ne doit survivre dans un
            # cache navigateur/proxy après déconnexion (bouton Retour).
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        if _requete_a_auditer(request.method, path, response.status_code):
            # Résolution d'acteur vérifiée (PostgreSQL + Redis) et écriture du
            # journal dans un thread : jamais d'accès base bloquant sur la
            # boucle d'événements, et jamais d'identité crue sur en-tête.
            await asyncio.to_thread(
                _enregistrer_audit_http,
                methode=request.method,
                chemin=path,
                code_statut=response.status_code,
                request_id=request_id,
                entetes=dict(request.headers),
                client_host=request.client.host if request.client else None,
                user_agent=request.headers.get("User-Agent"),
            )
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
                from .schema_metier import VERSION_SCHEMA_APPLICATION

                if (VERSION_SCHEMA_APPLICATION not in index._get_applied_migrations(conn)
                        or not index._schema_identite_octets_valide(conn)):
                    raise RuntimeError("Schéma d'identité des octets indisponible")
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
        file_operationnelle = file_durable_disponible(redis_store)
        file: dict[str, object] = {
            "configured": redis_store.is_configured(),
            "durable": file_operationnelle,
            "require_durable": config.require_durable_queue,
            "web_worker_enabled": config.web_worker_enabled,
        }
        if file_operationnelle:
            try:
                file.update(redis_store.profondeur_file("imports"))
                file["workers_vivants"] = [w.get("worker_id") for w in redis_store.workers_vivants()]
                file["dead_letters"] = redis_store.get_deadletters("imports", limit=20)
            except Exception as exc:
                logger.warning("Supervision de la file indisponible : %s", exc)

        # Supervision métier : l'exploitant doit voir, sans requête SQL, ce qui
        # est en attente, ce qui tourne, ce qui a échoué et pourquoi.
        jobs_par_statut: dict[str, int] = {}
        jobs_en_cours: list[dict[str, object]] = []
        try:
            jobs_par_statut = compter_jobs_par_statut(index)
            jobs_en_cours = jobs_actifs(index, limite=20)
        except Exception as exc:
            logger.warning("Comptage des jobs indisponible : %s", exc)

        sauvegarde = etat_sauvegarde(config)

        return {
            "status": "ok",
            **health_details,
            # Lot PROD : en incident, « quelle version tourne ? » se répond
            # sans ouvrir une session sur la machine. La clé est posée APRÈS
            # les détails d'index : aucun diagnostic (version PostgreSQL,
            # schéma…) ne peut plus l'écraser (MAN-01).
            "version": __version__,
            "disk_free_bytes": disk_usage.free,
            "disk_total_bytes": disk_usage.total,
            "documents": stats.total_documents,
            "files": stats.files,
            "folders": stats.folders,
            "last_scan": index.latest_scan(),
            "redis_connected": redis_store.ping() if redis_store.is_configured() else None,
            "storage_backend": config.storage_backend,
            "s3_configured": bool(config.s3_endpoint_url or config.s3_access_key),
            "s3_credentials": s3_credential_kind(config),
            "upload_dead_letters": len(file.get("dead_letters", [])) if isinstance(file.get("dead_letters"), list) else 0,
            "queue": file,
            "jobs": {
                "par_statut": jobs_par_statut,
                "echecs": jobs_par_statut.get("failed", 0),
                "en_cours": jobs_en_cours,
                "recoverables": jobs_par_statut.get("pending", 0),
            },
            "backup": sauvegarde,
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
        # File identity is the opaque catalogue id. Keep local paths, object
        # keys, and full parent directories inside the backend only.
        safe_results = []
        for result in results:
            parent = str(result.get("parent") or "").replace("\\", "/").rstrip("/")
            safe_parent = parent.rsplit("/", 1)[-1] if parent else ""
            safe_results.append(
                {
                    **{
                        key: value
                        for key, value in result.items()
                        if key not in {"path", "path_key", "object_key", "parent"}
                    },
                    "id": int(result["id"]),
                    "parent": safe_parent,
                }
            )
        return {
            "query": q,
            "count": len(safe_results),
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
            "results": safe_results,
        }

    @app.get("/metrics")
    def get_metrics(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        exiger_administrateur_requete(
            index,
            config,
            token=token,
            entete_utilisateur=entete_utilisateur,
            entete_role=entete_role,
            entete_session=entete_session,
            entete_jeton=entete_jeton,
        )
        return {**metrics, "health": health(token)}

    @app.get("/preview")
    def preview(
        path: str = Query(..., min_length=1, max_length=4_096),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, object]:
        """Explorateur historique par chemin — surface d'exploitation.

        Le navigateur utilise les pièces par identifiant (``/api/preview``
        répond 410). Côté backend PostgreSQL, ce parcours de fichiers par
        chemin est désormais réservé à un administrateur humain ; le mode
        SQLite conserve le contrat d'outillage historique.
        """
        _require_auth(config, token)
        exiger_administrateur_requete(
            index,
            config,
            token=token,
            entete_utilisateur=entete_utilisateur,
            entete_role=entete_role,
            entete_session=entete_session,
            entete_jeton=entete_jeton,
        )
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
        """Ouverture historique par chemin, réservée à l'outillage serveur.

        Le navigateur ne l'utilise plus (``/api/open`` = 410 ; pièces par ID).
        En PostgreSQL, le chemin doit en plus désigner un document CATALOGUÉ :
        être physiquement sous une racine ne suffit plus à rendre un fichier
        ouvrable. Les scripts de qualification qui ouvrent les originaux par
        leur chemin les prennent dans le catalogue, donc le contrat utile est
        conservé sans rouvrir une lecture arbitraire du système de fichiers.
        """
        _require_auth(config, token)
        _exiger_role_si_humain_depuis_requete(request, token, (ROLE_APPROBATEUR,))
        acteur = resoudre_acteur_audit(
            index, config, request.headers, request.client.host if request.client else None
        )
        actor = acteur.libelle_audit
        # Conserver l'identité logique demandée : résoudre un lien AVANT la
        # consultation du catalogue peut remplacer un document capturé par un
        # autre fichier de la même racine, sans SHA associé.
        target = _validated_path_lexical(path, config)

        # Le SHA d'octets, lorsqu'il existe, impose une vérification avant
        # toute réponse ou redirection ; il n'est pas le hash de texte.
        object_key = None
        object_bucket = None
        empreinte_capture = None
        document_trouve = False
        cle_chemin = os.path.normcase(str(target))
        try:
            with index.connect() as conn:
                if index.is_postgres:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            "SELECT object_key, object_bucket, source_sha256 FROM documents WHERE path_key = %s",
                            (cle_chemin,),
                        )
                        row = cursor.fetchone()
                        document_trouve = row is not None
                        if row:
                            object_key, object_bucket, empreinte_capture = row
                else:
                    row = conn.execute(
                        "SELECT object_key, object_bucket, source_sha256 FROM documents WHERE path_key = ?", (cle_chemin,)
                    ).fetchone()
                    document_trouve = row is not None
                    if row:
                        object_key, object_bucket, empreinte_capture = row
        except Exception as exc:
            # Une erreur de catalogue ne permet pas de conclure qu'aucune
            # identité capturée n'existe ; ne pas servir une autre version.
            logger.warning("Consultation du catalogue impossible (%s)", type(exc).__name__)
            raise HTTPException(status_code=503, detail="Catalogue temporairement indisponible.") from None

        if index.is_postgres and not document_trouve:
            record_audit_event(
                index,
                action="open",
                actor=actor,
                resource=str(target),
                status="404",
                details={"reason": "document_non_catalogue"},
                resource_type="document",
                resource_id=cle_chemin,
                code_statut=404,
                **acteur.contexte_audit(),
            )
            raise HTTPException(status_code=404, detail="Document non catalogué.")

        if empreinte_capture is not None:
            if not isinstance(empreinte_capture, str) or re.fullmatch(r"[0-9a-f]{64}", empreinte_capture) is None:
                raise HTTPException(status_code=409, detail="Identité du fichier invalide.")
            # Endpoint PUBLIC déclaré + objet prouvé : la redirection est permise.
            #
            # Le catalogue ne porte `object_key` que si l'objet a été lié SOUS
            # l'identité d'octets de la ligne (``lier_objets_capture`` : UPDATE
            # … WHERE source_sha256 = l'empreinte de l'envoi vérifié). L'objet
            # est donc CES octets, preuve à l'appui : une redirection le sert
            # sans revérification, exactement comme pour un document sans
            # empreinte. Sans cette preuve (objet d'un autre compartiment,
            # envoi non vérifié, ligne non liée), on garde le repli : l'API lit
            # et vérifie les octets contre le SHA de capture.
            # Honnêteté : cette preuve est datée (elle ne couvre pas une
            # réécriture ultérieure de l'objet) ; le repli, lui, revérifie.
            if (object_key and (object_bucket is None or object_bucket == (
                    storage_client.bucket_name if storage_client is not None else None))):
                url_capture = _url_presignee_navigateur(object_key, storage_client, config)
                if url_capture:
                    record_audit_event(
                        index,
                        action="open",
                        actor=actor,
                        resource=str(target),
                        status="302",
                        details={"object_key": object_key, "servi_par": "redirection_publique_capture"},
                    )
                    return RedirectResponse(url=url_capture, status_code=302)
            fd_verifie: int | None = None
            try:
                fd_source = _ouvrir_artefact_confine(target, [Path(root) for root in config.root_paths])
                with os.fdopen(fd_source, "rb") as flux:
                    fd_verifie = _copie_flux_verifiee(flux, empreinte_capture, config.max_file_size_bytes)
            except (OSError, ValueError):
                pass
            if (fd_verifie is None and storage_client is not None and object_key
                    and object_bucket == storage_client.bucket_name):
                try:
                    corps = storage_client.get_object(object_key)["Body"]
                    try:
                        fd_verifie = _copie_flux_verifiee(corps, empreinte_capture, config.max_file_size_bytes)
                    finally:
                        corps.close()
                except Exception as exc:
                    if fd_verifie is not None:
                        os.close(fd_verifie)
                        fd_verifie = None
                    logger.warning("Objet de catalogue non vérifiable (%s)", type(exc).__name__)
            if fd_verifie is None:
                raise HTTPException(status_code=404, detail="Fichier conforme non disponible.")
            try:
                record_audit_event(index, action="open", actor=actor, resource=str(target), status="200")
            except BaseException:
                os.close(fd_verifie)
                raise
            return _ReponseFichierEpingle(fd_verifie, filename=target.name)

        url_publique = _url_presignee_navigateur(object_key, storage_client, config)
        if url_publique:
            record_audit_event(
                index, action="open", actor=actor, resource=str(target), status="302", details={"object_key": object_key}
            )
            return RedirectResponse(url=url_publique, status_code=302)

        # La lecture suit le descripteur confiné, pas le chemin revérifié
        # puis rouvert plus tard par FileResponse.
        if target.exists() and target.is_file():
            try:
                fd = _ouvrir_artefact_confine(target, [Path(root) for root in config.root_paths])
            except (OSError, ValueError):
                raise HTTPException(status_code=409, detail="Lecture locale non sécurisée.") from None
            try:
                record_audit_event(index, action="open", actor=actor, resource=str(target), status="200")
            except BaseException:
                os.close(fd)
                raise
            return _ReponseFichierEpingle(fd, filename=target.name)

        # Ni redirection publique ni copie locale : l'objet existe pourtant
        # dans le stockage (cas d'une restauration) — on le sert par l'API.
        if storage_client is not None and object_key:
            record_audit_event(
                index,
                action="open",
                actor=actor,
                resource=str(target),
                status="200",
                details={"object_key": object_key, "servi_par": "proxy_api"},
            )
            return _reponse_objet_s3(
                object_key,
                target.name,
                "application/pdf" if target.suffix.lower() == ".pdf" else "application/octet-stream",
                storage_client,
                inline=True,
            )

        # For directories, return listing instead of trying OS open
        if target.exists() and target.is_dir():
            record_audit_event(index, action="open", actor=actor, resource=str(target), status="200")
            return JSONResponse({"opened": str(target), "is_dir": True, "note": "Directory listing via /preview"})

        if not target.exists():
            record_audit_event(index, action="open", actor=actor, resource=str(target), status="404")
            raise HTTPException(status_code=404, detail="Path does not exist.")

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

        cancel_check_local = make_cancel_checker(job_id)

        def cancel_check() -> bool:
            if cancel_check_local():
                return True
            job = get_job(index, job_id)
            return job is None or job.get("status") == "cancelled"

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
            intention = get_import(index, job_id)
            if intention and intention.get("publication_pending"):
                resultat_annule = {**intention, "job_id": job_id, "status": "cancelled",
                                   "error": "Import annulé ; publication incertaine à réconcilier."}
                update_job(index, job_id, status="cancelled", stage="cancelled",
                           error=resultat_annule["error"], result=resultat_annule)
            else:
                update_job(index, job_id, status="cancelled", stage="cancelled", error="Job was cancelled by user")
        except Exception as exc:
            logger.error("Import job %s failed (%s)", job_id, type(exc).__name__)
            intention = get_import(index, job_id)
            if intention and intention.get("publication_pending"):
                resultat_incertain = {**intention, "job_id": job_id, "status": "needs_review",
                                      "error": "Publication incertaine : réconciliation requise."}
                update_job(index, job_id, status="needs_review", stage="reconciliation",
                           error=resultat_incertain["error"], result=resultat_incertain)
            else:
                update_job(index, job_id, status="failed", stage="failed",
                           error=f"Import impossible ({type(exc).__name__}).")
        finally:
            clear_job_cancel(job_id)

    def _reponse_intention_incertaine(job_id: str, actor: str, action: str, exc: Exception) -> JSONResponse | None:
        try:
            intention = get_import(index, job_id)
        except Exception as lecture_exc:
            logger.warning("État de l'import inaccessible (%s)", type(lecture_exc).__name__)
            return None
        if not intention or not intention.get("publication_pending"):
            return None
        logger.error("Import synchrone interrompu (%s)", type(exc).__name__)
        record_audit_event(index, action=action, actor=actor, resource=job_id,
                           status="needs_review", details={"sync": True})
        return JSONResponse(status_code=200, content={
            **intention, "job_id": job_id, "status": "needs_review",
            "error": "Publication incertaine : réconciliation requise.",
        })

    @app.post("/imports")
    async def create_import(
        request_body: ImportRequest,
        request: Request,
        wait: bool = Query(False),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> Response:
        _require_auth(config, token)
        _exiger_role_si_humain_depuis_requete(
            request, token, (ROLE_ADMIN, ROLE_UTILISATEUR, ROLE_APPROBATEUR)
        )
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
            except Exception as exc:
                # Fournir l'identifiant de toute intention persistée plutôt
                # qu'inciter à recréer un import après une écriture ambiguë.
                incertain = _reponse_intention_incertaine(job_id, actor, "import_create", exc)
                if incertain is not None:
                    return incertain
                if isinstance(exc, InsufficientStorageError):
                    record_audit_event(index, action="import_create", actor=actor, resource=job_id, status="507")
                    raise HTTPException(status_code=507, detail=str(exc)) from exc
                if isinstance(exc, PermissionError):
                    record_audit_event(index, action="import_create", actor=actor, resource=job_id, status="403")
                    raise HTTPException(status_code=403, detail=str(exc)) from exc
                if isinstance(exc, ValueError):
                    record_audit_event(index, action="import_create", actor=actor, resource=job_id, status="400")
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                raise

        try:
            resolved = Path(request_body.source_path).expanduser().resolve()
            if not resolved.exists() or not resolved.is_dir():
                raise ValueError("Import path must be an existing directory")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        task_payload = {
            "job_id": job_id,
            "source_path": request_body.source_path,
            "selected_pdf": None,
            "selected_excel": request_body.excel_file,
        }
        durable = file_durable_disponible(redis_store)
        if not durable and config.require_durable_queue:
            record_audit_event(
                index,
                action="import_create",
                actor=actor,
                resource=job_id,
                status="503",
                details={"reason": "file durable indisponible"},
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "File d'attente durable indisponible (Redis injoignable) : l'import est refusé "
                    "plutôt qu'accepté en mémoire. Réessayez quand le service est revenu, "
                    "ou démarrez le worker (docker compose up -d redis worker)."
                ),
            )
        create_job(
            index,
            job_id,
            request_body.source_path,
            status="pending",
            stage="queued",
            durability="durable" if durable else "process_memory",
            # Sélections MANUELLES persistées en base (A06) : sans elles, une
            # reprise après perte de la charge Redis ré-important un AUTRE
            # document que celui désigné par l'opérateur.
            selected_pdf=task_payload.get("selected_pdf"),
            selected_excel=task_payload.get("selected_excel"),
        )
        if durable:
            redis_store.set_job(
                job_id,
                {"id": job_id, "status": "pending", "progress": 0, "stage": "queued", "source_path": request_body.source_path},
            )
            redis_store.enqueue_task("imports", task_payload)
        else:
            logger.warning(
                "Redis indisponible : job %s accepté EN MÉMOIRE DE PROCESSUS (repli de développement). "
                "Conséquence : il ne survivra pas à un redémarrage du serveur.",
                job_id,
            )
            task = asyncio.create_task(asyncio.to_thread(_execute_import_background, job_id, source, None, excel_path))
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)

        record_audit_event(
            index,
            action="import_create",
            actor=actor,
            resource=job_id,
            status="accepted",
            details={"async": True, "durability": "durable" if durable else "process_memory"},
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
                "durability": "durable" if durable else "process_memory",
                "durable": durable,
            },
        )

    @app.post("/imports/scan")
    async def scan_import(
        request_body: ImportScanRequest,
        request: Request,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        _exiger_role_si_humain_depuis_requete(
            request, token, (ROLE_ADMIN, ROLE_UTILISATEUR, ROLE_APPROBATEUR)
        )
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
            raise HTTPException(status_code=403, detail="Accès au dossier interdit.") from exc
        except ValueError as exc:
            record_audit_event(
                index, action="import_scan", actor=actor, resource=request_body.source_path, status="400"
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            record_audit_event(
                index, action="import_scan", actor=actor, resource=request_body.source_path, status="409"
            )
            raise HTTPException(status_code=409, detail="Le dossier ne peut pas être analysé en sécurité.") from exc

    @app.post("/imports/confirm")
    async def confirm_import(
        request_body: ImportConfirmRequest,
        request: Request,
        wait: bool = Query(True),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> Response:
        _require_auth(config, token)
        _exiger_role_si_humain_depuis_requete(
            request, token, (ROLE_ADMIN, ROLE_UTILISATEUR, ROLE_APPROBATEUR)
        )
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
            except Exception as exc:
                incertain = _reponse_intention_incertaine(job_id, actor, "import_confirm", exc)
                if incertain is not None:
                    return incertain
                if isinstance(exc, InsufficientStorageError):
                    raise HTTPException(status_code=507, detail=str(exc)) from exc
                if isinstance(exc, PermissionError):
                    raise HTTPException(status_code=403, detail=str(exc)) from exc
                if isinstance(exc, ValueError):
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                raise

        task_payload = {
            "job_id": job_id,
            "source_path": request_body.source_path,
            "selected_pdf": str(technical_pdf),
            "selected_excel": str(excel_path) if excel_path else None,
        }
        durable = file_durable_disponible(redis_store)
        if not durable and config.require_durable_queue:
            record_audit_event(
                index,
                action="import_confirm",
                actor=actor,
                resource=job_id,
                status="503",
                details={"reason": "file durable indisponible"},
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "File d'attente durable indisponible (Redis injoignable) : l'import est refusé "
                    "plutôt qu'accepté en mémoire. Réessayez quand le service est revenu."
                ),
            )
        create_job(
            index,
            job_id,
            request_body.source_path,
            status="pending",
            stage="queued",
            durability="durable" if durable else "process_memory",
            # Sélections MANUELLES persistées en base (A06) : sans elles, une
            # reprise après perte de la charge Redis ré-important un AUTRE
            # document que celui désigné par l'opérateur.
            selected_pdf=task_payload.get("selected_pdf"),
            selected_excel=task_payload.get("selected_excel"),
        )
        if durable:
            redis_store.set_job(
                job_id,
                {"id": job_id, "status": "pending", "progress": 0, "stage": "queued", "source_path": request_body.source_path},
            )
            redis_store.enqueue_task("imports", task_payload)
        else:
            logger.warning(
                "Redis indisponible : import %s accepté EN MÉMOIRE DE PROCESSUS (repli de développement).",
                job_id,
            )
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
            details={"async": True, "durability": "durable" if durable else "process_memory"},
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
                "durability": "durable" if durable else "process_memory",
                "durable": durable,
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
        _exiger_role_si_humain_depuis_requete(
            request, token, (ROLE_ADMIN, ROLE_UTILISATEUR, ROLE_APPROBATEUR)
        )
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

        # A05 — l'espace de noms de destination est validé EN ENTIER avant la
        # moindre écriture. Deux fichiers qui visent le même chemin ne peuvent
        # pas être « acceptés tous les deux » : le second écraserait le premier
        # et l'API répondrait 200. On refuse explicitement, avec la liste
        # complète des conflits — rien n'est écrit, donc rien n'est perdu.
        plan = [_safe_relative_path(upload.filename or "file") for upload in files]
        collisions = analyser_collisions(plan)
        if collisions:
            record_audit_event(
                index,
                action="import_upload",
                actor=actor,
                resource=str(staged),
                status="409",
                details={"collisions": collisions[:20], "nb_fichiers": len(plan)},
            )
            logger.warning(
                "Envoi refusé : %d conflit(s) de destination — aucun fichier écrit (%s).",
                len(collisions),
                staged,
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "collision_chemins",
                    "message": (
                        f"{len(collisions)} conflit(s) de nom de fichier dans cet envoi : rien n'a été "
                        "écrit. Renommez ou déplacez les fichiers concernés, puis renvoyez."
                    ),
                    "collisions": collisions,
                },
            )

        staged.mkdir(parents=True, exist_ok=True)
        total_size = 0
        max_aggregate = config.max_file_size_bytes * 10  # aggregate cap: 10x single file
        try:
            for upload, relative in zip(files, plan):
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
        except PermissionError as exc:
            shutil.rmtree(staged, ignore_errors=True)
            raise HTTPException(status_code=400, detail="Accès au dossier interdit.") from exc
        except ValueError as exc:
            shutil.rmtree(staged, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            shutil.rmtree(staged, ignore_errors=True)
            raise HTTPException(status_code=409, detail="Le dossier ne peut pas être analysé en sécurité.") from exc
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
        # Une régénération ou publication interrompue peut avoir laissé des
        # effets externes ; leur état persistant prime aussi sur le cache.
        if record and (record.get("report_generation_pending") or record.get("publication_pending")):
            # Une intention persistée contredit nécessairement un éventuel
            # ancien « completed », y compris à l'intérieur de result.
            import_en_revue = {**record, "status": "needs_review"} if record.get("status") == "completed" else record
            reponse = {**(job or {}), **import_en_revue, "job_id": (job or {}).get("id", import_id),
                       "result": import_en_revue}
            if job:
                reponse["job_status"] = job.get("status")
                # C'est l'état effectivement stocké, distinct de l'état
                # opérationnel corrigé quand une intention est incertaine.
                reponse["import_status"] = record.get("status")
                if job.get("status") != "completed":
                    reponse["status"] = job["status"]
                    if job.get("error"):
                        reponse["error"] = job["error"]
            return reponse

        if redis_store.is_configured() and job is None and record is None:
            cached = redis_store.get_job(import_id)
            if cached:
                # Le cache n'est qu'un repli si aucun état durable n'existe.
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
                # Un résultat d'import plus récent peut corriger un job réussi,
                # mais il ne peut pas transformer un job actif, échoué ou en
                # revue en succès. Exposer les deux états évite l'ambiguïté.
                etat_job = job["status"]
                etat_import = record.get("status")
                etat = (etat_import or etat_job) if etat_job == "completed" else etat_job
                reponse = {**job, **record, "job_id": job["id"], "status": etat,
                           "job_status": etat_job, "import_status": etat_import}
                if etat_job != "completed" and job.get("error"):
                    reponse["error"] = job["error"]
                return reponse
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
        _exiger_role_si_humain_depuis_requete(
            request, token, (ROLE_ADMIN, ROLE_UTILISATEUR, ROLE_APPROBATEUR)
        )
        actor = actor_fingerprint(token, request.client.host if request.client else None)
        updated = cancel_job(index, import_id)
        if updated is None:
            rec = get_import(index, import_id)
            audit_status = "409" if rec is not None else "404"
        else:
            audit_status = "success" if updated.get("status") == "cancelled" else "409"
        record_audit_event(
            index, action="import_cancel", actor=actor, resource=import_id, status=audit_status,
        )
        if audit_status == "404":
            raise HTTPException(status_code=404, detail="Import not found.")
        if audit_status == "409":
            raise HTTPException(status_code=409, detail="Import already finished or not cancellable.")
        return updated

    @app.patch("/imports/{import_id}")
    async def patch_import(
        import_id: str,
        request_body: ImportCorrectionRequest,
        request: Request,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        _exiger_role_si_humain_depuis_requete(request, token, (ROLE_APPROBATEUR,))
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
                    logger.warning("Redis cache refresh after import correction failed (%s)", type(exc).__name__)
            record_audit_event(index, action="import_correct", actor=actor, resource=import_id, status="success")
            return res
        except KeyError as exc:
            incertain = _reponse_intention_incertaine(import_id, actor, "import_correct", exc)
            if incertain is not None:
                return incertain
            record_audit_event(index, action="import_correct", actor=actor, resource=import_id, status="404")
            raise HTTPException(status_code=404, detail="Import not found.") from exc
        except PublicationIncertaine as exc:
            record_audit_event(index, action="import_correct", actor=actor, resource=import_id, status="409")
            raise HTTPException(status_code=409, detail="Publication incertaine : réconciliation requise avant correction.") from exc
        except ImportMutationIncertitude as exc:
            record_audit_event(index, action="import_correct", actor=actor, resource=import_id, status="503")
            raise HTTPException(status_code=503, detail="Résultat incertain : le verrou de l'import a été perdu.") from exc
        except ImportMutationEnCours as exc:
            record_audit_event(index, action="import_correct", actor=actor, resource=import_id, status="409")
            raise HTTPException(status_code=409, detail="Une opération est déjà en cours sur cet import.") from exc
        except Exception as exc:
            # La génération ou l'envoi peut avoir échoué après un effet S3.
            # Une intention durable prime sur le type de l'exception : ne pas
            # livrer une erreur générique qui invite à recommencer à l'aveugle.
            incertain = _reponse_intention_incertaine(import_id, actor, "import_correct", exc)
            if incertain is not None:
                return incertain
            if isinstance(exc, ValueError):
                record_audit_event(index, action="import_correct", actor=actor, resource=import_id, status="400")
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            raise

    @app.post("/imports/{import_id}/retry-upload")
    async def retry_import_upload(
        import_id: str,
        request: Request,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        _exiger_role_si_humain_depuis_requete(
            request, token, (ROLE_ADMIN, ROLE_UTILISATEUR, ROLE_APPROBATEUR)
        )
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
                    logger.warning("Redis cache refresh after upload retry failed (%s)", type(exc).__name__)
            record_audit_event(index, action="import_retry_upload", actor=actor, resource=import_id, status="success")
            return res
        except KeyError as exc:
            incertain = _reponse_intention_incertaine(import_id, actor, "import_retry_upload", exc)
            if incertain is not None:
                return incertain
            record_audit_event(index, action="import_retry_upload", actor=actor, resource=import_id, status="404")
            raise HTTPException(status_code=404, detail="Import not found.") from exc
        except PublicationIncertaine as exc:
            record_audit_event(index, action="import_retry_upload", actor=actor, resource=import_id, status="409")
            raise HTTPException(status_code=409, detail="Publication incertaine : réconciliation requise avant reprise.") from exc
        except ImportMutationIncertitude as exc:
            record_audit_event(index, action="import_retry_upload", actor=actor, resource=import_id, status="503")
            raise HTTPException(status_code=503, detail="Résultat incertain : le verrou de l'import a été perdu.") from exc
        except ImportMutationEnCours as exc:
            record_audit_event(index, action="import_retry_upload", actor=actor, resource=import_id, status="409")
            raise HTTPException(status_code=409, detail="Une opération est déjà en cours sur cet import.") from exc
        except RapportsEnRegeneration as exc:
            record_audit_event(index, action="import_retry_upload", actor=actor, resource=import_id, status="409")
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            incertain = _reponse_intention_incertaine(import_id, actor, "import_retry_upload", exc)
            if incertain is not None:
                return incertain
            if isinstance(exc, (ValueError, OSError)):
                record_audit_event(index, action="import_retry_upload", actor=actor, resource=import_id, status="409")
                raise HTTPException(status_code=409, detail="Reprise refusée : source ou rapport indisponible ou non sûr.") from exc
            raise

    @app.get("/imports/{import_id}/artifacts/{artifact}")
    def get_import_artifact(
        import_id: str,
        artifact: str,
        request: Request,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> Response:
        _require_auth(config, token)
        acteur = exiger_acteur_humain(
            index,
            config,
            token=token,
            entete_utilisateur=entete_utilisateur,
            entete_role=entete_role,
            entete_session=entete_session,
            entete_jeton=entete_jeton,
            client_host=request.client.host if request.client else None,
        )
        actor = acteur.libelle_audit

        def _audit_artefact(status: str, details: dict[str, Any] | None = None) -> None:
            record_audit_event(
                index,
                action="artifact_download",
                actor=actor,
                resource=f"{import_id}/{artifact}",
                status=status,
                details=details or {},
                request_id=getattr(request.state, "request_id", None),
                resource_type="import_artifact",
                resource_id=f"{import_id}/{artifact}",
                **acteur.contexte_audit(),
            )

        allowed = {"report_pdf", "report_docx", "source_pdf", "source_excel"}
        if artifact not in allowed:
            raise HTTPException(status_code=400, detail=f"Unknown artifact. Allowed: {', '.join(sorted(allowed))}")

        # Exception technique Lot 1, distincte des pièces : un rapport généré
        # peut être relu par admin ou approbateur ; un fichier source reste
        # réservé à l'approbateur. Un utilisateur ne peut donc pas contourner
        # l'approbation de pièce par les artefacts d'import, et lecture ne
        # télécharge rien. L'acteur de service SQLite historique est conservé.
        roles_artefact = (
            (ROLE_ADMIN, ROLE_APPROBATEUR)
            if artifact in {"report_pdf", "report_docx"}
            else (ROLE_APPROBATEUR,)
        )
        if acteur.est_humain and acteur.role not in roles_artefact:
            _audit_artefact("403", {"reason": "role_not_authorized", "roles": list(roles_artefact)})
            raise HTTPException(
                status_code=403,
                detail=f"Téléchargement de cet artefact réservé au(x) rôle(s) {', '.join(roles_artefact)}.",
            )

        payload = get_import(index, import_id)
        if payload is None:
            _audit_artefact("404")
            raise HTTPException(status_code=404, detail="Import not found.")
        if artifact in {"report_pdf", "report_docx"} and payload.get("report_generation_pending"):
            _audit_artefact("409", {"reason": "report_generation_incomplete"})
            raise HTTPException(status_code=409, detail="Régénération des rapports inachevée.")

        # Resolve file path and object key from payload
        file_path: Path | None = None
        object_key: str | None = None
        # Vrai quand l'objet retenu prouve l'identité CAPTURÉE du fichier
        # (envoi vérifié, SHA égal) : voir la redirection présignée plus bas.
        identite_capture_prouvee = False
        filename: str = ""
        media_type: str = "application/octet-stream"

        if artifact == "report_pdf":
            file_path = Path(payload["report_path"]) if payload.get("report_path") else None
            filename = "technical-report.pdf"
            media_type = "application/pdf"
        elif artifact == "report_docx":
            file_path = Path(payload["report_docx_path"]) if payload.get("report_docx_path") else None
            filename = "technical-report.docx"
            media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif artifact == "source_pdf":
            file_path = Path(payload["technical_pdf"]) if payload.get("technical_pdf") else None
            filename = Path(file_path).name if file_path else "source.pdf"
            media_type = "application/pdf"
        elif artifact == "source_excel":
            file_path = Path(payload["excel_file"]) if payload.get("excel_file") else None
            filename = Path(file_path).name if file_path else "source.xlsx"
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

        empreinte_capture: str | None = None
        limite_copie = config.max_file_size_bytes
        if artifact in {"report_pdf", "report_docx"} and payload.get("report_generation_pending"):
            _audit_artefact("409", {"reason": "report_generation_pending"})
            raise HTTPException(status_code=409, detail="Rapport en cours de régénération.")

        if file_path is not None and artifact in {"source_pdf", "source_excel"}:
            fichiers_correspondants = [
                f for f in payload.get("files", [])
                if isinstance(f, dict) and f.get("path") == str(file_path)
            ]
            empreintes = [f.get("source_sha256") for f in fichiers_correspondants
                          if f.get("source_sha256") is not None]
            if empreintes:
                if (len(fichiers_correspondants) != 1 or len(empreintes) != 1
                        or not isinstance(empreintes[0], str)
                        or re.fullmatch(r"[0-9a-f]{64}", empreintes[0]) is None):
                    _audit_artefact("409", {"reason": "invalid_capture_identity"})
                    raise HTTPException(status_code=409, detail="Identité de la source invalide.")
                empreinte_capture = empreintes[0]

        if file_path is not None:
            # Les rapports récents portent eux aussi un SHA de génération ou de
            # publication. Les anciens rapports sans identité restent hérités.
            from .import_pipeline import _preuve_artefact_valide, _preuve_identifiable, construire_manifeste

            manifeste = construire_manifeste(payload)
            if artifact in {"report_pdf", "report_docx"}:
                limite_copie = max(config.max_file_size_bytes, 64 * 1024 * 1024)
                rapports = [a for a in payload.get("artifacts", [])
                            if isinstance(a, dict) and a.get("path") == str(file_path)]
                if len(rapports) > 1:
                    _audit_artefact("409", {"reason": "ambiguous_report_identity"})
                    raise HTTPException(status_code=409, detail="Identité du rapport ambiguë.")
                entree_rapport = next((e for e in manifeste if e.get("path") == str(file_path)), None)
                if entree_rapport is not None:
                    empreinte = entree_rapport.get("expected_sha256")
                    if empreinte is None and _preuve_identifiable(entree_rapport):
                        empreinte = entree_rapport.get("sha256")
                    if empreinte is not None:
                        if not isinstance(empreinte, str) or re.fullmatch(r"[0-9a-f]{64}", empreinte) is None:
                            _audit_artefact("409", {"reason": "invalid_report_identity"})
                            raise HTTPException(status_code=409, detail="Identité du rapport invalide.")
                        empreinte_capture = empreinte

            # Une clé seule peut correspondre à un envoi échoué ou ambigu.
            for entree in manifeste if storage_client is not None else []:
                if (entree.get("path") == str(file_path)
                        and entree.get("bucket") == storage_client.bucket_name
                        and _preuve_artefact_valide(entree)
                        and (empreinte_capture is None or (
                            _preuve_identifiable(entree) and entree.get("sha256") == empreinte_capture
                        ))):
                    object_key = entree["key"]
                    # L'objet ne peut être retenu avec une empreinte de capture
                    # que si son SHA vérifié EST cette empreinte : la preuve
                    # d'identité est donc déjà faite (envoi vérifié, SHA égal).
                    identite_capture_prouvee = empreinte_capture is not None
                    break

        if file_path is not None and not _chemin_artefact_autorise(file_path):
            # Le payload ne peut pas désigner un fichier hors racines : on
            # neutralise le chemin local (lecture ET cache S3). Une éventuelle
            # clé d'objet rattachée à cet import reste servable par l'API ou
            # par redirection publique, mais jamais écrite hors confinement.
            logger.warning(
                "Chemin d'artefact hors racines refusé pour l'import %s (%s).",
                import_id,
                artifact,
            )
            file_path = None

        # Redirection présignée UNIQUEMENT vers un endpoint public déclaré : le
        # endpoint interne (nom d'hôte « minio » du réseau des conteneurs, port
        # 9000) n'est résolvable depuis AUCUN poste de l'atelier. Par défaut on
        # sert donc les octets par l'API.
        #
        # Une source capturée (staging promu, P0.2) porte une empreinte : une
        # URL directe ne peut pas, elle, RE-vérifier les octets au moment de la
        # lecture. Mais l'objet retenu ci-dessus n'est éligible qu'avec un envoi
        # VÉRIFIÉ dont le SHA EST l'empreinte de capture : l'identité est déjà
        # prouvée, et refuser la redirection condamnerait alors tout déploiement
        # qui déclare un endpoint public (R2/CloudFront) à faire transiter
        # chaque octet par l'API. Dès que cette preuve manque — empreinte sans
        # objet vérifié, objet d'un autre compartiment, envoi échoué — on garde
        # le repli : l'API sert les octets et les vérifie contre le SHA initial.
        # Honnêteté : la preuve d'envoi est datée (elle ne couvre pas une
        # réécriture ultérieure de l'objet) ; le repli local, lui, revérifie.
        url_publique = (_url_presignee_navigateur(object_key, storage_client, config)
                        if (empreinte_capture is None or identite_capture_prouvee) else None)
        if url_publique:
            _audit_artefact(
                "302",
                {"object_key": object_key, "filename": filename, "servi_par": "redirection_publique"},
            )
            return RedirectResponse(url=url_publique, status_code=302)

        # Ouvrir par descripteur confiné. Pour une source capturée, copier et
        # vérifier les OCTETS qui seront servis : le descripteur seul n'empêche
        # pas une écriture concurrente dans l'inode ouvert.
        if file_path is not None:
            try:
                racines_locales = [Path(root) for root in config.root_paths]
                racines_locales.extend([staging_root(config), quarantine_root(config), Path(config.database_path).parent / "reports"])
                fd = _ouvrir_artefact_confine(file_path, racines_locales)
                if empreinte_capture is not None:
                    with os.fdopen(fd, "rb") as flux:
                        fd = _copie_flux_verifiee(flux, empreinte_capture, limite_copie)
            except (OSError, ValueError):
                pass  # Aucun repli par chemin : essayer l'objet distant seulement.
            else:
                try:
                    _audit_artefact("200", {"servi_par": "fichier_local_epingle"})
                except BaseException:
                    os.close(fd)
                    raise
                return _ReponseFichierEpingle(fd, filename=filename, media_type=media_type)

        # Ne jamais écrire un cache S3 dans un chemin source ou de rapport :
        # son parent peut avoir été substitué et l'écriture écraser un original.
        if storage_client is not None and object_key:
            fd_objet: int | None = None
            try:
                if empreinte_capture is not None:
                    corps = storage_client.get_object(object_key)["Body"]
                    try:
                        fd_objet = _copie_flux_verifiee(corps, empreinte_capture, limite_copie)
                    finally:
                        corps.close()
                    _audit_artefact("200", {"servi_par": "objet_verifie"})
                    fd_pour_reponse, fd_objet = fd_objet, None  # le constructeur ferme en cas d'erreur
                    return _ReponseFichierEpingle(fd_pour_reponse, filename=filename, media_type=media_type)
                reponse = _reponse_objet_s3(object_key, filename, media_type, storage_client)
                try:
                    _audit_artefact("200", {"servi_par": "proxy_api"})
                except BaseException:
                    reponse.fermer()
                    raise
                return reponse
            except Exception as exc:
                logger.warning("Téléchargement d'artefact objet impossible (%s)", type(exc).__name__)
            finally:
                if fd_objet is not None:
                    os.close(fd_objet)

        _audit_artefact("404")
        raise HTTPException(status_code=404, detail="Artifact not found. It may not have been generated or uploaded yet.")

    # ---------------------------------------------------------------------------
    # Maintenance & Audit Endpoints
    # ---------------------------------------------------------------------------

    @app.post("/maintenance/replay-deadletters")
    def replay_deadletters(
        request: Request,
        limit: int = Query(10, ge=1, le=100),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        acteur = exiger_administrateur_requete(
            index,
            config,
            token=token,
            entete_utilisateur=entete_utilisateur,
            entete_role=entete_role,
            entete_session=entete_session,
            entete_jeton=entete_jeton,
            client_host=request.client.host if request.client else None,
        )
        actor = acteur.libelle_audit
        if not redis_store.is_configured():
            raise HTTPException(status_code=503, detail="Redis not configured")
        count = redis_store.replay_deadletters("imports", limit=limit)
        record_audit_event(
            index,
            action="replay_deadletters",
            actor=actor,
            status="success",
            details={"replayed": count},
            request_id=getattr(request.state, "request_id", None),
            resource_type="maintenance",
            resource_id="deadletters",
            **acteur.contexte_audit(),
        )
        return {"status": "ok", "replayed": count}

    @app.get("/maintenance/deadletters")
    def list_deadletters(
        request: Request,
        limit: int = Query(50, ge=1, le=200),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        exiger_administrateur_requete(
            index,
            config,
            token=token,
            entete_utilisateur=entete_utilisateur,
            entete_role=entete_role,
            entete_session=entete_session,
            entete_jeton=entete_jeton,
            client_host=request.client.host if request.client else None,
        )
        if not redis_store.is_configured():
            raise HTTPException(status_code=503, detail="Redis not configured")
        dead = redis_store.get_deadletters("imports", limit=limit)
        return {"count": len(dead), "results": dead}

    @app.post("/maintenance/cleanup")
    async def maintenance_cleanup(
        request: Request,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        acteur = exiger_administrateur_requete(
            index,
            config,
            token=token,
            entete_utilisateur=entete_utilisateur,
            entete_role=entete_role,
            entete_session=entete_session,
            entete_jeton=entete_jeton,
            client_host=request.client.host if request.client else None,
        )
        actor = acteur.libelle_audit
        res = await asyncio.to_thread(run_retention_cleanup, config, index)
        record_audit_event(
            index,
            action="maintenance_cleanup",
            actor=actor,
            status="success",
            details=res,
            request_id=getattr(request.state, "request_id", None),
            resource_type="maintenance",
            resource_id="cleanup",
            **acteur.contexte_audit(),
        )
        return {"status": "ok", "cleanup": res}

    @app.get("/audit")
    def list_audit_events(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0, le=1_000_000),
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        entete_utilisateur: Annotated[str | None, Header(alias="X-SEAMTECH-UTILISATEUR")] = None,
        entete_role: Annotated[str | None, Header(alias="X-SEAMTECH-ROLE")] = None,
        entete_session: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION")] = None,
        entete_jeton: Annotated[str | None, Header(alias="X-SEAMTECH-SESSION-JETON")] = None,
    ) -> dict[str, object]:
        _require_auth(config, token)
        exiger_administrateur_requete(
            index,
            config,
            token=token,
            entete_utilisateur=entete_utilisateur,
            entete_role=entete_role,
            entete_session=entete_session,
            entete_jeton=entete_jeton,
        )
        logs = get_audit_logs(index, limit=limit, offset=offset)
        return {
            "count": len(logs),
            "offset": offset,
            "limit": limit,
            "results": logs,
        }

    # Lot B.2 (§17.11) : traçabilité des champs et registre de gabarits —
    # lecture et publication uniquement, aucune écriture de fiche par ces routes.
    # Lot L.1 (§17.4) : détection de doublons — PROPOSITIVE (liens seulement,
    # jamais de fusion ni d'effacement). Les routes vivent sous /fiches, la
    # ressource concernée, et alimentent le bandeau de /validation.
    #
    # ENREGISTRÉ AVANT les routes de fiche : `GET /fiches/{code}` (ci-dessous)
    # capturait `/fiches/doublons` et répondait « Fiche doublons inconnue » —
    # l'écran des doublons affichait une 404 alors que le scan répondait 200.
    # FastAPI résout dans l'ordre d'enregistrement ; mesuré en sandbox le
    # 2026-10-30 sur la pile réelle (API + interface Next).
    from seamtech_search.dedup.routes import enregistrer_routes_dedup

    enregistrer_routes_dedup(app, index, config, _require_auth)

    from seamtech_search.fiches.routes import enregistrer_routes_fiches

    enregistrer_routes_fiches(app, index, config, _require_auth, storage_client=storage_client)

    # Lot E (§17.2, §17.5) : recherche hybride des fiches — GET /recherche et
    # GET /recherche/suggestions. L'existant (/search fichiers) n'est pas touché.
    # Lot F : l'encodeur e5 ONNX est chargé s'il est sur le disque (poids
    # téléchargés explicitement — jamais au runtime) ; sinon encode_requete
    # reste None et la branche vectorielle demeure dormante, sans erreur.
    from seamtech_search.recherche import enregistrer_routes_recherche

    optionnels_actifs = fonctionnalites_optionnelles_actives()
    encodeur_ml = _charger_encodeur_ml(config) if optionnels_actifs else None
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
    if optionnels_actifs:
        from seamtech_search.assistant import enregistrer_routes_assistant

        enregistrer_routes_assistant(app, index, config, _require_auth, metriques=metrics)

    from seamtech_search.qualite.routes import enregistrer_routes_qualite

    enregistrer_routes_qualite(app, index, config, _require_auth)

    # Lot L.2 (§10.1, §17.5) : authentification nominative — /auth/connexion,
    # /auth/deconnexion, /auth/session, gestion des comptes. Ces routes exigent
    # le jeton de service : le navigateur ne les appelle jamais directement, il
    # passe par le proxy Next.js, qui seul détient SEAMTECH_AUTH_TOKEN.
    from seamtech_search.comptes.routes import enregistrer_routes_auth

    enregistrer_routes_auth(app, index, config, _require_auth)

    # Lot 1 : demandes de téléchargement/modification/suppression et
    # notifications applicatives. Le navigateur passe exclusivement par les
    # relais Next.js /api/approvals et /api/notifications ; ces routes backend
    # revérifient le jeton de service puis la session nominative et son rôle.
    from seamtech_search.approbations import enregistrer_routes_approbations

    enregistrer_routes_approbations(app, index, config, _require_auth)

    # Lot 3b : file et détail des extractions de voiles, résolution du modèle
    # de bateau vers le référentiel canonique (Lot 2), corrections approbateur
    # et apprentissage local (alias + motifs suggérés). Le navigateur passe
    # par le relais Next.js /api/extractions-voile ; ces routes revérifient le
    # jeton de service puis la session nominative (mutations : approbateur).
    from seamtech_search.extractions_voile_routes import enregistrer_routes_extractions_voile

    enregistrer_routes_extractions_voile(app, index, config, _require_auth)

    # Lot 7 : file et détail des extractions d'accessoires + reclassification
    # — même contrat d'acteurs que le Lot 3b (lecture nominative, mutation
    # approbateur), servi au navigateur par le relais /api/accessoires.
    from seamtech_search.accessoires_routes import enregistrer_routes_accessoires

    enregistrer_routes_accessoires(app, index, config, _require_auth)

    # Lot 4b (Fonction A) : comparaison de voiles bloc par bloc sur les
    # valeurs vérifiées du Lot 3b — lecture seule, session nominative.
    # Le navigateur passe par le relais Next.js /api/comparaison-voiles.
    from seamtech_search.comparaison_voile_routes import enregistrer_routes_comparaison_voile

    enregistrer_routes_comparaison_voile(app, index, config, _require_auth)

    # Lot 5 (Fonction B) : confiance de réutilisation (médiane/MAD/IQR,
    # justification obligatoire, évaluation approbateur persistée en 030).
    # Le navigateur passe par le relais Next.js /api/confiance-reutilisation.
    from seamtech_search.confiance_reutilisation_routes import (
        enregistrer_routes_confiance_reutilisation,
    )

    enregistrer_routes_confiance_reutilisation(app, index, config, _require_auth)

    if optionnels_actifs:
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


def _url_presignee_navigateur(
    object_key: str | None,
    storage_client: S3StorageClient | None,
    config: AppConfig,
) -> str | None:
    """URL présignée **uniquement si un endpoint public est déclaré**.

    Une redirection présignée vers l'endpoint INTERNE (le service « minio » du
    réseau des conteneurs) casse le téléchargement depuis un autre poste de
    l'atelier : ce nom d'hôte n'existe que dans le réseau des conteneurs. Constaté par le test Compose du
    job CI `integration` (302 → « Temporary failure in name resolution »).

    Par défaut, la fonction renvoie donc ``None`` et l'appelant sert les octets
    par l'API authentifiée. Renseigner ``SEAMTECH_S3_PUBLIC_ENDPOINT_URL``
    (endpoint réellement joignable par les navigateurs) rétablit la
    redirection.
    """
    if storage_client is None or not object_key or not config.s3_public_endpoint_url:
        return None
    try:
        return storage_client.get_presigned_url(
            object_key, expiration_seconds=900, endpoint_url=config.s3_public_endpoint_url
        )
    except Exception as exc:  # pragma: no cover - dépend du fournisseur
        logger.warning("Signature publique impossible pour %s : %s", object_key, exc)
        return None


def _reponse_objet_s3(
    object_key: str,
    filename: str,
    media_type: str,
    storage_client: S3StorageClient | None,
    *,
    inline: bool = False,
) -> _ReponseFluxFerme:
    """Sert un objet S3 **à travers l'API** (proxy authentifié).

    C'est le mode par défaut des téléchargements : l'URL vue par le navigateur
    reste celle de l'application, donc joignable depuis n'importe quel poste,
    sans exposer l'endpoint interne ni un jeton porteur présigné.
    """
    if storage_client is None:
        raise HTTPException(status_code=503, detail="Stockage objet non configuré.")
    nom_sans_controle = "".join(c for c in filename if ord(c) >= 32 and ord(c) != 127 and c not in "/\\") or "document"
    # L'en-tête doit rester ASCII et ne jamais contenir de retour à la ligne ;
    # filename* permet aux navigateurs de retrouver le nom Unicode.
    nom_encode = quote(nom_sans_controle, safe="", errors="replace")
    disposition = "inline" if inline else "attachment"
    entetes = {"Content-Disposition": f"{disposition}; filename=\"document\"; filename*=UTF-8''{nom_encode}"}
    entete = storage_client.head_object(object_key)
    longueur = entete.get("ContentLength")
    if longueur is not None:
        longueur = int(longueur)
        if longueur < 0:
            raise ValueError("Taille d'objet invalide")
        entetes["Content-Length"] = str(longueur)
    corps = storage_client.get_object(object_key)["Body"]

    def _blocs() -> Iterator[bytes]:
        yield from iter(lambda: corps.read(1024 * 1024), b"")

    return _ReponseFluxFerme(corps, _blocs(), media_type=media_type, headers=entetes)


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


def _cle_insensible(chemin: Path) -> str:
    """Clé de comparaison INSENSIBLE À LA CASSE, indépendante de l'hôte.

    Un serveur Linux et un poste Windows ne doivent pas répondre différemment au
    même envoi : sur Windows ``Rapport.pdf`` et ``rapport.pdf`` désignent le même
    fichier, donc accepter les deux reviendrait à en perdre un en silence. On
    refuse PARTOUT, pas seulement là où le système d'exploitation veut bien le
    signaler.
    """
    return "/".join(part.casefold() for part in chemin.parts)


def analyser_collisions(relatifs: list[Path]) -> list[dict[str, Any]]:
    """Conflits de destination d'un envoi groupé — détectés AVANT toute écriture.

    Défaut corrigé (A05 de l'audit du 2026-10-08) : deux fichiers nommés
    ``same.txt`` étaient acceptés, écrits l'un après l'autre dans la même
    destination, et l'API répondait 200 — l'opérateur croyait avoir déposé deux
    pièces et n'en avait plus qu'une. Trois familles de conflits, toutes
    rencontrées en vrai :

    * ``doublon`` : deux entrées visent le même chemin (mêmes noms, noms rendus
      identiques par l'assainissement ``:`` → ``_``, ou différences de casse) ;
    * ``fichier_vs_dossier`` : un fichier porte le nom d'un dossier du même
      envoi (``a`` et ``a/b.txt``) — écrire l'un détruirait l'autre ;
    * ``dossier_implicite`` : deux fichiers exigent le même dossier avec des
      casses différentes.

    La liste est COMPLÈTE (pas seulement le premier conflit) : l'opérateur
    renomme en une fois au lieu de découvrir les collisions une par une.
    """
    conflits: list[dict[str, Any]] = []
    vues: dict[str, Path] = {}
    for chemin in relatifs:
        cle = _cle_insensible(chemin)
        if cle in vues:
            conflits.append(
                {
                    "type": "doublon",
                    "cle": cle,
                    "chemins": [str(vues[cle]), str(chemin)],
                    "message": (
                        f"« {vues[cle]} » et « {chemin} » visent le même fichier de destination "
                        "(nom identique, ou identique après assainissement/insensibilité à la casse)"
                    ),
                }
            )
        else:
            vues[cle] = chemin

    # Un chemin ne peut pas être à la fois un fichier et un dossier du même envoi.
    for cle, chemin in vues.items():
        parties = cle.split("/")
        for profondeur in range(1, len(parties)):
            prefixe = "/".join(parties[:profondeur])
            if prefixe in vues:
                conflits.append(
                    {
                        "type": "fichier_vs_dossier",
                        "cle": prefixe,
                        "chemins": [str(vues[prefixe]), str(chemin)],
                        "message": (
                            f"« {vues[prefixe]} » est un fichier, mais « {chemin} » en fait un dossier "
                            "— la destination ne peut pas être les deux"
                        ),
                    }
                )
    return conflits


def _require_auth(config: AppConfig, token: str | None) -> None:
    if not config.auth_token:
        return
    if token is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    import secrets

    # Constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(token.encode("utf-8"), config.auth_token.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Authentication required.")


def _validated_path_lexical(path: str, config: AppConfig) -> Path:
    """Autorisation lexicale pour /open ; l'ouverture physique refuse les liens."""
    target = Path(os.path.abspath(Path(path).expanduser()))
    allowed_roots = [Path(os.path.abspath(root.expanduser())) for root in config.root_paths]
    if not any(target == root or root in target.parents for root in allowed_roots):
        raise HTTPException(status_code=403, detail="Path is outside configured search roots.")
    return target


def _validated_path(path: str, config: AppConfig, *, exiger_existence: bool = True) -> Path:
    """Résout un chemin et vérifie qu'il reste DANS les racines autorisées.

    ``/open`` utilise au contraire une validation lexicale séparée, car
    l'identité du catalogue ne doit pas être résolue à travers un lien. La
    vérification de confinement (403) reste ici inconditionnelle.
    """
    target = Path(path).expanduser().resolve()
    allowed_roots = [root.resolve() for root in config.root_paths]
    if exiger_existence and not target.exists():
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
