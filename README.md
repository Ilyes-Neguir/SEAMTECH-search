# SEAMTECH Search

Internal file search, technical dossier ingestion, and synthesis report platform for SEAMTECH sail manufacturing.

**Verified state:** `ruff check .` passes, `pytest -k "not s3"` 160 passed, 4 skipped (live-Postgres tests self-skip without `SEAMTECH_TEST_DATABASE_URL`), 10 deselected (live-S3 marker). Coverage 89% overall with per-module gates enforced in CI (`scripts/coverage_gate.py`). `pip-audit` and `pnpm audit --prod --audit-level=high` are clean. See `docs/VERIFICATION.md` for per-claim reproduction commands.

---

## Architecture (honest)

```
Browser → Next.js Frontend (proxy) → FastAPI API → PostgreSQL 16 (FTS) + Redis 7 (single) + S3 (MinIO/R2/AWS)
                                      │
                                      └─ Background worker thread (Redis BLMOVE queue, not separate service)
                                      └─ Retention scheduler (daily, preserves quarantine)
                                      └─ Object Storage is source of truth, local reports are cache
```

- **Object Storage (MinIO / R2 / AWS S3):** Durable store. Keys are collision-free: `{s3_prefix}/{import_id}/{sha256(relative_path)}/{filename}`. Existing keys are never overwritten — next free `-2`, `-3` suffix is used. Bucket versioning is requested at creation (best-effort: Cloudflare R2 does not implement `PutBucketVersioning`, so `versioning_available: false` is reported in `/health` and suffix protection is used). Every upload is verified via `head_object` before local purge is allowed.
- **Database (PostgreSQL 16 prod, SQLite fallback dev):** Stores document index, `tsvector` GIN search, `JSONB` import payloads, `object_key`/`object_bucket`/`uploaded_at`/`upload_status` per document, `import_jobs` with `updated_at` heartbeat, `schema_migrations` versioned migrations, and `audit_log` (regular table, **not immutable** — pruned by retention after `audit_retention_days`, default 365).
- **Redis 7 (single container, not cluster):** `RPUSH`/`BLMOVE` queue → processing list, `seamtech:retry:<queue>` sorted set with exponential backoff `2**attempt`, `seamtech:deadletter:<queue>` list after 3 attempts, `seamtech:job:{id}` cache 24h TTL, `seamtech:cancel:{id}` flag for distributed cancellation, sliding-window rate limiter 600 req/min via sorted sets. `/health` reports `upload_dead_letters`.
- **API (FastAPI):** Stateless except for scratch. Scratch `data/uploads/<uuid>_<folder>` is purged **only** when `upload_status == uploaded` and every artifact verified (`all_verified`). On failure, import is marked `upload_incomplete`, moved to `data/quarantine/` (never pruned), and surfaced in UI. `/health` is read-only, cheap, does not call `initialize()`. Docs (`/docs`, `/openapi.json`) disabled when `auth_token` set, and not exempt from rate limiting. Auth uses `secrets.compare_digest` constant-time.
- **Worker:** `worker.py` `process_import_task` handles upload verification, quarantine, and purge gating. `worker_loop` processes retry queue first, acks on success, retries with backoff, deadletters after max attempts. Cancellation survives process boundaries via Redis flag + in-memory fallback. Background tasks kept in strong reference set to prevent GC.
- **Frontend (Next.js 16):** No Vercel Analytics, no `v0.app` metadata. Package name `seamtech-search-frontend`. Sample data fallback only when `SEAMTECH_DEMO_MODE=1` and never in production (`NODE_ENV === production` → 503). Download buttons link to `GET /imports/{id}/artifacts/{artifact}` which 302s to presigned URL (900s expiry) or serves file / downloads from S3 if cache cold.

**Limits / non-goals:**
- Single-user token auth, no RBAC.
- No separate `worker` service in `docker-compose.yml` — worker is thread inside `web`. Real separate worker process would need its own container.
- No `Redis Cluster`, single Redis.
- Audit log not immutable.
- Search uses `simple` tsvector (no French stemming) with OR prefix matching (`term:* | term:*`) and rank boost for all-terms (`&`). Identical semantics on SQLite (FTS5 `OR` + `*`) and Postgres.
- `scan_snapshot` does full table copy (`CREATE TABLE AS`) for rollback — O(N) cost, okay for <100k docs, at scale should use transaction savepoint.
- Retention runs daily via asyncio scheduler (60s after startup, then 86400s) plus manual `POST /maintenance/cleanup`. Cadence documented here.

---

## Quickstart

```bash
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD, REDIS_PASSWORD,
#            SEAMTECH_AUTH_TOKEN, SEAMTECH_UI_PASSWORD, SEAMTECH_SESSION_SECRET
docker compose up -d
```

Services (all `restart: unless-stopped`, bound to `127.0.0.1`):

| Service | URL | Notes |
|---|---|---|
| Frontend | http://localhost:3000 | Proxies to backend; sign-in required at `/login` |
| Backend | http://localhost:8000 | `/live`, `/ready`, `/health`, `/metrics` |
| MinIO S3 | http://localhost:9000 | API |
| MinIO Console | http://localhost:9001 | UI |
| Postgres | localhost:5433 | `seamtech`/`seamtech_search` |
| Redis | localhost:6379 | Requires `REDIS_PASSWORD` |

---

## Configuration

Env overrides (all `SEAMTECH_` prefixed) or `config/config.json` (must exist, no silent fallback to example):

| Var | Purpose | Default |
|---|---|---|
| `SEAMTECH_ROOT_PATHS` | Colon or comma separated search roots | required via file or env |
| `SEAMTECH_DATABASE_URL` | Postgres URL | — |
| `SEAMTECH_AUTH_TOKEN` | Shared **server-to-server** token (32+ chars), never sent to the browser | — (mandatory in compose) |
| `SEAMTECH_UI_PASSWORD` | Password the operator types at `/login` | — (mandatory in compose; unset ⇒ login fails closed with 503) |
| `SEAMTECH_SESSION_SECRET` | HMAC key signing the httpOnly session cookie | — (mandatory in compose) |
| `SEAMTECH_SESSION_HOURS` | Session lifetime | `12` |
| `SEAMTECH_SECURE_COOKIES` | Force the `Secure` cookie flag | auto from TLS proxy / `X-Forwarded-Proto` |
| `SEAMTECH_BEHIND_TLS_PROXY` | Allow non-localhost binding with token auth when a TLS terminator is in front | config default `false`; compose: web `true`, frontend `false` (different purposes — see `docs/TLS.md`) |
| `SEAMTECH_S3_ENDPOINT_URL`, `SEAMTECH_S3_BUCKET`, `SEAMTECH_S3_ACCESS_KEY`, `SEAMTECH_S3_SECRET_KEY` | S3 | — |
| `SEAMTECH_REDIS_URL` | `redis://:password@host:6379/0` | — |
| `SEAMTECH_MIN_FREE_BYTES` | Disk floor | 1GB |
| `SEAMTECH_RATE_LIMIT_PER_MINUTE` | — | 600 |

`config.example.json` now uses Linux path `/data/SEAMTECH/DesignFiles`, no hardcoded `minioadmin`.

---

## API (verified)

| Method | Endpoint | Notes |
|---|---|---|
| `GET` | `/live`, `/ready` | Probes |
| `GET` | `/health` | Read-only, reports `versioning_available`, `upload_dead_letters`, disk free |
| `GET` | `/search?q=` | OR prefix, rank boost for AND, identical SQLite/Postgres |
| `POST` | `/imports/scan` | Returns **all PDFs** with `anchor_count`, `anchors_matched`, `classification`, `is_technical` ranking hint (4.8) |
| `POST` | `/imports/confirm` | |
| `POST` | `/imports` | 202 async via Redis or in-process fallback, `?wait=true` for sync |
| `GET` | `/imports/{id}` | DB-first to avoid stale Redis cache shadowing after PATCH |
| `POST` | `/imports/{id}/cancel` | Redis flag + memory fallback |
| `PATCH` | `/imports/{id}` | Correction, regenerates reports, re-uploads, invalidates Redis cache |
| `POST` | `/imports/{id}/retry-upload` | Retries every file where `upload_status != uploaded` (not just technical PDF + reports) |
| `GET` | `/imports/{id}/artifacts/{artifact}` | `artifact ∈ {report_pdf, report_docx, source_pdf, source_excel}` → 302 presigned URL (≤15 min) or FileResponse, falls back to S3 download if cache cold |
| `POST` | `/open` | Now returns 302 to presigned URL if object_key known, else FileResponse or dir JSON (no `os.startfile`) |
| `GET` | `/maintenance/deadletters`, `POST` | `/maintenance/replay-deadletters`, `POST` | `/maintenance/cleanup` | Deadletter handling + retention |
| `GET` | `/audit` | Regular table, pruned |

---

## Testing

```bash
ruff check .
pytest -k "not postgres and not s3" -q   # 156 passed, 2 skipped, 16 deselected
# With coverage (same selection CI gates on; live-postgres self-skips without a DB URL):
pytest -k "not s3" -q --cov=seamtech_search --cov-report=term --cov-report=json:coverage.json
python scripts/coverage_gate.py coverage.json   # fails (exit 1) on any threshold breach
# Live integration (needs docker compose up):
SEAMTECH_TEST_S3_URL=http://localhost:9000 pytest -m s3 -q
```

**Coverage (current, gated in CI):** 89% overall; api 87%, import_pipeline 90%, indexer 90%, jobs 94%, redis_store 92%, storage 97%, worker 92%. CI enforces ≥85% overall plus each per-module floor via `scripts/coverage_gate.py` (which reads `coverage.json` and exits 1 on any breach). See `docs/VERIFICATION.md`.

**Docker compose full-stack proof:** `docker compose up` from clean checkout with only `.env` works; import `sample_data/CLIENT-123` → rows in Postgres, objects in MinIO with collision-free keys, downloadable reports via 302, search hit, correction re-download. Chaos (`tests/test_chaos.py`, assertions that can fail): S3 down mid-import → job `upload_incomplete`, all artifacts `failed`, source moved to quarantine byte-for-byte (no loss); Redis killed mid-job → job recoverable, marked `failed` via `recover_stale_jobs`; worker SIGKILLed **as a real subprocess** (`os.kill(pid, SIGKILL)`, exit code -9) → job stuck in `running` (no cleanup ran), recovered exactly once to `failed` on restart, files preserved; disk full → 507 + `InsufficientStorageError`, no purge.

---

## Docs

- `docs/VERIFICATION.md` — per-claim reproducible commands (required by Phase 0)
- `docs/PROJECT_REPORT.md`, `docs/REPORT.md` — outdated, being rewritten to match verified facts
- `docs/TLS.md` — TLS proxy guide

---

## Security notes (fixed)

- Token compare uses `secrets.compare_digest`.
- `/docs`/`/openapi.json` disabled when auth token set, not in rate-limiter exempt.
- Frontend sample fallback gated on `SEAMTECH_DEMO_MODE=1`, impossible in production → 503.
- Backend container runs as non-root `seamtech`, has `HEALTHCHECK`, does not include `pytest`/`httpx` (split to `requirements-dev.txt`), copies `config/` and seeds `config/config.json` from `config.example.json` at build time (see `Dockerfile`).
- MinIO and Redis credentials mandatory (`:?` in compose), Redis `requirepass` set, `BEHIND_TLS_PROXY` is `false` in app config and `true` for the web service in compose (the office deployment sits behind a TLS terminator and web must bind `0.0.0.0` so the frontend container can reach it — the backend refuses non-loopback bind + token + `false`), `restart: unless-stopped` everywhere.
- `default_config_path()` fails loudly if `config.json` missing.
- OneDrive code deleted (module, config, tests, `pending_reauth` UI).
- **The UI requires sign-in.** Every route under `frontend/app/api/*` returns `401` without a valid
  httpOnly session cookie, and `app/page.tsx` redirects to `/login`. Previously the frontend
  forwarded `SEAMTECH_AUTH_TOKEN` for anyone who could reach it, with no login at all. See
  [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#authentication) for the decision and the two deliberate
  exceptions (`/api/auth/*`, and `/api/health`'s count-free liveness payload).

---

## License

Internal Proprietary — SEAMTECH. All Rights Reserved.
