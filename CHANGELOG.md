# Changelog

## 0.4.0 — Decoupled Cloud-Native Architecture & Production Hardening

Major architectural transformation from local/OneDrive storage to a decoupled, cloud-native architecture with S3-compatible object storage, Redis task queues, sliding-window rate limiting, and multi-technical-PDF extraction.

### Cloud-Native Storage & Stateless VPS

- **S3 / MinIO / Cloudflare R2 Storage** (`seamtech_search/storage.py`): Object storage client supporting MinIO, Cloudflare R2, and AWS S3 with bucket auto-provisioning, multi-part uploads, and secure presigned URL generation.
- **Stateless Scratch Purging**: Uploads and raw staged files are immediately cleared from host disk after S3 upload, preventing local disk accumulation and ensuring zero customer data loss on VPS restarts.
- **Dossier Upload Pipeline**: Uploads all technical drawing sheets, Excel BOM spreadsheets, and both generated PDF/Word synthesis reports under standardized S3 object keys.

### Asynchronous Queue & Distributed Rate Limiting

- **Redis Task Queue & Fast-Path Cache** (`seamtech_search/redis_store.py`): Replaced in-memory thread loops with Redis `RPUSH` / `BLPOP` FIFO queues. Caches job progress in Redis keys (`seamtech:job:{id}`) with 24-hour TTL to prevent database bottlenecks during frontend polling.
- **Dedicated Worker Loop** (`seamtech_search/worker.py`): Background worker daemon managing task execution, progress stage reporting, artifact uploads, and scratch directory cleanup.
- **Sliding-Window Rate Limiting**: Distributed rate limiter enforcing 600 req/min via Redis sorted sets (`ZADD`, `ZREMRANGEBYSCORE`, `ZCARD`) with HTTP 429 and `Retry-After` headers; container probe endpoints are exempt.

### Multi-Technical-PDF Analysis & Dual Reporting

- **Multi-Sheet Technical Extraction** (`seamtech_search/import_pipeline.py`): Extracted parameters across all secondary technical PDF sheets into `additional_sheets` and `analyzed_items`.
- **Synchronized Dual Synthesis Reports**: Produces branded PDF reports (`reportlab`) and editable Word DOCX reports (`python-docx`) detailing primary and secondary technical sheets and BOM tables.
- **Tabular BOM Extraction**: Layout-aware table extraction using `openpyxl` with automatic unit and dimension normalization.

### Security, Auditing & TLS Hardening

- **Append-Only Audit Logging** (`seamtech_search/audit.py`): Immutable audit logging for mutating operations and search queries. Actor tokens are SHA-256 fingerprinted so secrets are never logged. Accessible via `GET /audit`.
- **Non-Local TLS Enforcement**: Refuses non-localhost binding when authentication is enabled unless `behind_tls_proxy=true`.
- **Pre-Flight Disk Guard** (`seamtech_search/retention.py`): Verifies available storage against `min_free_bytes` (1 GB) before accepting uploads.
- **Database Connection Pooling** (`seamtech_search/indexer.py`): Implemented `ThreadedConnectionPool` for PostgreSQL with statement timeouts (`statement_timeout_ms`).

### Frontend & Infrastructure

- **Next.js 16 Web UI**: Dynamic candidate selection with anchor evidence, live stage progress polling, cancellation controls, and direct presigned download links.
- **Docker Compose**: Multi-container stack orchestration for PostgreSQL 16 (5433), MinIO S3 (9000/9001), Redis 7 (6379), FastAPI Backend (8000), and Next.js Frontend (3000).
- **Test Suite**: 93 automated tests (91 passed, 2 skipped live daemon markers) and clean `ruff` linter pass.

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
