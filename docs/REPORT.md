# SEAMTECH Search Production Readiness & Operational Report

## 1. Executive Summary

SEAMTECH Search v0.4.0 is a fully hardened, decoupled, cloud-native file search, extraction, and technical dossier analysis platform.

All components have been upgraded from local desktop paradigms to scalable, distributed cloud infrastructure:
- **Permanent Storage:** S3-compatible object storage (MinIO locally, Cloudflare R2 or AWS S3 in production).
- **Relational Metadata & Full-Text Search:** PostgreSQL 16 with `ThreadedConnectionPool`, `tsvector`/GIN indexing, and JSONB document storage.
- **Coordination & Rate Limiting:** Redis 7 for distributed task queues (`RPUSH`/`BLPOP`), fast-path job status caching, and sliding-window rate limiting.
- **Stateless Compute:** Ephemeral scratch directory lifecycle with automatic purge upon object storage upload.
- **Multi-Technical-PDF Analysis:** Full extraction across all technical PDF drawings in multi-sheet dossiers with dual PDF/Word synthesis reports.

---

## 2. Implemented Architecture & Subsystems

### Storage & Upload Architecture (`seamtech_search/storage.py`)
- S3 client supporting MinIO, Cloudflare R2, and AWS S3 with standard bucket initialization and presigned download URL generation.
- Complete dossier ingestion uploading raw technical drawings, Excel workbooks, and generated synthesis reports.
- Stateless VPS operations: local scratch staging is purged immediately after S3 upload.

### Distributed Task Queue & Worker (`seamtech_search/redis_store.py` & `worker.py`)
- Background task queue using Redis `RPUSH` / `BLPOP`.
- Fast-path status caching in Redis (`seamtech:job:{id}`) with 24-hour TTL, mitigating database load during UI progress polling.
- Standalone background worker process supporting asynchronous execution (`POST /imports` returning HTTP 202 Accepted), progress stage updates, and cooperative cancellation (`POST /imports/{id}/cancel`).

### Multi-Sheet Technical Analysis & Dual Reporting (`seamtech_search/import_pipeline.py`)
- Two-phase anchor detection for technical drawings (`surface`, `guindant`, `bordure`, `chute`, `longueur`, `largeur`, `matériau`, `grammage`, `finition`, `mesures dessin`).
- Structured parsing of all secondary technical PDFs into `additional_sheets` and `analyzed_items`.
- Tabular BOM extraction with `openpyxl`.
- Dual synthesis report generation producing styled PDFs (`reportlab`) and Word documents (`python-docx`).

### Security, Auditing & TLS Hardening
- Sliding-window rate limiter (default 600 req/min) returning HTTP 429 with `Retry-After` headers; probes (`/live`, `/ready`, `/health`, `/metrics`) are exempted.
- Non-local network binding with authentication requires `behind_tls_proxy=true` to prevent cleartext token leakage over LAN networks.
- Append-only audit logging (`seamtech_search/audit.py`) with token fingerprinting (SHA-256 prefixes) to prevent credential leakage.
- Explicit lifecycle state separation between `pending_reauth` (credential renewal required) and `pending_retry` (transient network backoff).

### Database Connection Pooling (`seamtech_search/indexer.py`)
- PostgreSQL `ThreadedConnectionPool` with statement timeouts (`statement_timeout_ms`) and pooled context managers.
- Schema tables for `documents`, `scans`, `imports`, `import_jobs`, and `audit_log` with automatic JSONB migration support.

---

## 3. Operational Endpoints & API Reference

| Endpoint | Method | Purpose |
|---|---|---|
| `/live` | `GET` | Container liveness probe |
| `/ready` | `GET` | Container readiness probe (PostgreSQL & Redis check) |
| `/health` | `GET` | Detailed system, storage, and database health metrics |
| `/metrics` | `GET` | Search latency and request performance statistics |
| `/search` | `GET` | Paginated full-text search with highlighted snippets |
| `/imports/scan` | `POST` | Scan folder for technical candidate files |
| `/imports/confirm`| `POST` | Confirm and execute import pipeline on selected candidate |
| `/imports` | `POST` | Submit asynchronous import job (HTTP 202 Accepted) |
| `/imports/{id}` | `GET` | Poll job status, progress, and download URLs |
| `/imports/{id}/cancel` | `POST` | Cooperatively cancel a running import job |
| `/imports/{id}` | `PATCH`| Submit manual corrections, regenerate reports, and re-upload |
| `/imports/{id}/retry-upload` | `POST` | Re-attempt failed artifact uploads |
| `/audit` | `GET` | Inspect append-only audit trail |
| `/maintenance/cleanup` | `POST` | Prune expired reports, uploads, and audit records |

---

## 4. Verification & Testing Summary

```text
============================= test session starts ==============================
platform linux -- Python 3.11.2, pytest-8.4.1
collected 93 items

91 passed, 2 skipped (Postgres/S3 live daemon integration markers)
Ruff check: 0 errors, 0 warnings
TypeScript check (frontend): passed
Playwright E2E test suite: passed
```

### Key Test Suites:
- `tests/test_storage.py`: Object storage S3/MinIO client, presigned URLs, multi-sheet dossier uploads.
- `tests/test_redis.py`: Distributed rate limiting, Redis task queues, fast-path job caching.
- `tests/test_import_pipeline.py`: Technical extraction, dimension normalization, dual PDF/Word generation.
- `tests/test_import_workflow.py`: Two-phase scan/confirm, upload staging, manual correction, retry backoff.
- `tests/test_jobs_api.py`: Async job submission (202), status polling, and cancellation.
- `tests/test_pooling.py`: PostgreSQL connection pool lifecycle and statement timeouts.

---

## 5. Deployment Instructions

### Docker Compose (Full Cloud-Native Stack)
```bash
# Copy template and configure passwords/keys
cp .env.example .env

# Launch PostgreSQL 16, MinIO, Redis 7, Backend, and Frontend
docker compose up -d
```

### Production Checklist
1. Point `SEAMTECH_S3_ENDPOINT_URL` to production Cloudflare R2 / AWS S3 endpoint.
2. Provide `SEAMTECH_S3_ACCESS_KEY` and `SEAMTECH_S3_SECRET_KEY`.
3. Provide `SEAMTECH_DATABASE_URL` pointing to PostgreSQL 16.
4. Set `SEAMTECH_REDIS_URL` to production Redis instance.
5. Set `SEAMTECH_AUTH_TOKEN` to a secure 32+ character random string.
6. Terminate TLS at the reverse proxy (Nginx / Caddy / Traefik) and set `SEAMTECH_BEHIND_TLS_PROXY=true`.
