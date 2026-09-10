# Changelog

## 0.4.0 — Production Hardening & Async Job Architecture

Production-readiness hardening across the backend and frontend for local workshop pilot and LAN/TLS deployment.

### Backend

- **Async Job Architecture** (`seamtech_search/jobs.py`): Default `POST /imports` returns `202 Accepted` with a job ID. Progress is polled via `GET /imports/{id}`, and running jobs can be cooperatively cancelled via `POST /imports/{id}/cancel`. Legacy synchronous behavior is preserved with `?wait=true`. Stale running/pending jobs are automatically recovered on server restart.
- **Dedicated Microsoft Graph Client** (`seamtech_search/onedrive.py`): Full OAuth2 refresh-token lifecycle using raw `urllib` calls (no SDK dependencies). Features proactive token refresh, `0600` token cache permissions, rotated-token persistence, and explicit `pending_reauth` status (human credential refresh needed) distinct from transient `pending_retry`.
- **Database Connection Pooling** (`seamtech_search/indexer.py`): Implemented `ThreadedConnectionPool` for PostgreSQL with statement timeouts (`statement_timeout_ms`). Added pooled connection context managers and schema tables for `import_jobs` and `audit_log` across both SQLite and PostgreSQL.
- **Retention & Disk Guard** (`seamtech_search/retention.py`): Pre-flight disk space guard checks available bytes (`min_free_bytes`) and raises HTTP 507 Insufficient Storage when storage is low. Automated retention cleanup prunes old reports (default 90d), staged uploads (default 7d), and audit records (default 365d) via `POST /maintenance/cleanup` or CLI `cleanup`.
- **Append-Only Audit Logging** (`seamtech_search/audit.py`): Records all mutating operations and search queries. Actor tokens are safely fingerprinted (SHA-256 hash prefix) so secrets are never logged. Accessible via `GET /audit`.
- **Rate Limiting & Probes** (`seamtech_search/api.py`): Sliding-window rate limiter (default 600 req/min) returning HTTP 429 with `Retry-After` headers. `X-Request-ID` middleware for end-to-end tracing. Health probes `/live` and `/ready` for container orchestrators.
- **TLS Enforcement**: Non-local network binding with token authentication requires `behind_tls_proxy=true` to prevent plaintext credential exposure on LAN networks.

### Frontend

- **Import Panel**: Integrated job polling with live stage progress bar, cancel action, and dedicated `pending_reauth` alert banner when Microsoft Graph credentials expire.
- **Proxy Endpoints**: Added `/api/imports/[id]/cancel` Next.js proxy route.
- **Type Definitions**: Added `ImportJobPayload` and `OneDriveStatus` types.

### Infrastructure, Tooling & Tests

- **Ruff**: Configured standard `[tool.ruff]` linting (py312, line-length 120, E/F/I rules) with a clean pass across the entire codebase.
- **Docker Compose Hardening**: Default port mappings bound to `127.0.0.1` on loopback.
- **CI / CD Workflow**: Upgraded GitHub Actions matrix for Python 3.12 and 3.13, live PostgreSQL service container smoke step, ruff check, and Playwright E2E job.
- **E2E Testing**: Added Playwright test scaffolding and specs (`search.spec.ts`, `import.spec.ts`).
- **Test Suite**: 75 passed, 1 skipped unit & integration test suite covering pooling, OneDrive flows, fixtures, jobs API, and retention.

---

## 0.3.0 — Import workflow completion

Closes every gap from the import-pipeline audit; the search/crawl core is
unchanged apart from the shared anchor constant and the PDF extractor bump.

### Backend

- **Shared anchors** (`seamtech_search/anchors.py`): one canonical
  `TECHNICAL_ANCHORS` constant used by both the crawler and the import
  pipeline — classification can no longer drift. Added `reference` /
  `référence`, `longueur`, `largeur`, `matériau`, `mesures dessin`.
- **pdfplumber extraction** (`extractors.py`, extractor version bumped 3 → 4):
  layout-aware text plus table cells, with `pypdf` kept as an automatic
  fallback. Old PDFs are re-parsed on the next scan via version gating.
- **Unit normalization**: dimensions keep their raw values and gain `*_mm`
  normalized values plus `unit_normalized`; labeled `Longueur:/Largeur:`
  sheets are recognized alongside `L x W` patterns.
- **Two-phase import**: `POST /imports/scan` (read-only candidates with
  matched anchors) + `POST /imports/confirm` (process the chosen PDF).
  `POST /imports` keeps one-shot behaviour and now returns candidates too.
- **Word reports**: every import generates `technical-report.pdf` **and**
  `technical-report.docx` (python-docx).
- **OneDrive**: uploads all 3 files with exponential-backoff retries
  (`onedrive_max_retries`, env `SEAMTECH_UPLOAD_MAX_RETRIES`) plus
  `POST /imports/{id}/retry-upload` for later re-attempts.
- **Manual correction**: `PATCH /imports/{id}` re-validates with Pydantic,
  regenerates both reports and re-uploads.
- **JSONB storage**: `imports.payload` is `JSONB` on PostgreSQL with an
  automatic `TEXT → JSONB` migration; SQLite stays JSON text. Same JSON
  shape is returned on both backends.
- **Browser upload**: `POST /imports/upload` stages drag-and-drop bytes in an
  isolated server directory (path-escape hardened) and returns candidates.

### Frontend

- Import panel rewritten: server-path scan, candidate picker with anchor
  evidence, drag-and-drop zone, file/folder browse, upload-and-scan,
  result table with normalized dimensions, manual correction form and
  OneDrive retry button.
- New proxied API routes: `/api/imports/scan`, `/api/imports/confirm`,
  `/api/imports/upload`, `/api/imports/[id]` (GET + PATCH),
  `/api/imports/[id]/retry`.

### Tests

- `tests/test_import_workflow.py`: 21 tests over real reportlab-generated
  PDFs — anchors, units, scan/confirm, docx readability, correction API,
  3-file upload with mocked retry/backoff, and upload staging.
- Suite: **54 passed, 1 skipped** (skip = Postgres integration without a
  live DB), `tsc --noEmit` clean, Next.js production build green.
