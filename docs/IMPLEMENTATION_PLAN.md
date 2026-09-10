# SEAMTECH Search — Implementation Plan & Milestone Status

## Objective

Deliver an enterprise-grade, cloud-native file search, extraction, and engineering dossier analysis platform with decoupled storage, background processing queues, and full multi-sheet technical extraction.

---

## Architectural & Safety Principles

1. **Decoupled & Stateless VPS:** Ephemeral scratch directories are purged immediately upon S3/MinIO upload. Customer data and generated reports reside safely in object storage.
2. **Deterministic Multi-Sheet Analysis:** Dossiers with multiple technical drawing sheets extract parameters across all drawings without silent skips.
3. **Guaranteed Index Integrity:** Index updates are atomic and batched. Failed scans never purge historical records.
4. **Defense-in-Depth Security:** Non-local network bindings enforce TLS proxying. Token authentication is required for network modes. Audit logging cryptographically fingerprints tokens.
5. **Asynchronous & Responsive:** API endpoints respond immediately (HTTP 202 Accepted) with background task queues and live progress polling.

---

## Milestone Breakdown & Delivery Status

### Phase 1: Core Search & Crawling [COMPLETED]
- [x] Recursive directory crawler with symlink cycle protection and root containment validation.
- [x] Text and document extractors (`pypdf`, `python-docx`, structured text).
- [x] SQLite fallback with FTS5 search.
- [x] Initial FastAPI REST endpoints and Next.js frontend search interface.

### Phase 2: PostgreSQL & Scalable Full-Text Search [COMPLETED]
- [x] PostgreSQL 16 database backend with `tsvector` stemming and GIN indexing.
- [x] `ThreadedConnectionPool` with statement timeout guards.
- [x] Single-owner indexing lock and scan snapshot restoration on error.
- [x] Batched upserts and incremental re-index skip based on file modification times.

### Phase 3: Advanced Two-Phase Import & Technical Extraction [COMPLETED]
- [x] Two-phase import workflow: `POST /imports/scan` (read-only candidate discovery) and `POST /imports/confirm`.
- [x] Layout-aware extraction with `pdfplumber` and tabular BOM extraction with `openpyxl`.
- [x] Canonical anchor definitions in `seamtech_search/anchors.py`.
- [x] Dual-format report generation producing synchronized PDF (`reportlab`) and Word (`python-docx`) synthesis reports.
- [x] Interactive correction form (`PATCH /imports/{id}`) and upload retry backoff.
- [x] Drag-and-drop browser upload staging with path-traversal prevention.

### Phase 4: Production Hardening, Async Jobs & Security [COMPLETED]
- [x] Asynchronous import architecture: `POST /imports` (HTTP 202) with Redis queue, job polling, and cooperative cancellation (`POST /imports/{id}/cancel`).
- [x] Microsoft Graph OAuth2 client with proactive token refresh and explicit `pending_reauth` status.
- [x] Pre-flight disk space guard (`min_free_bytes`) and retention pruning for reports, uploads, and audit records.
- [x] Append-only audit logging (`audit_log` table) with SHA-256 token fingerprinting.
- [x] Sliding-window rate limiter (600 req/min) with probe exemptions.
- [x] Non-local network binding security enforcing TLS proxying.
- [x] Ruff linting and CI/CD GitHub Actions pipeline with Python 3.12/3.13 matrix and Playwright E2E testing.

### Phase 5: Cloud-Native Decoupled Architecture & Multi-Sheet Analysis [COMPLETED]
- [x] S3-compatible Object Storage (`seamtech_search/storage.py`) supporting MinIO, Cloudflare R2, and AWS S3 with presigned download URLs.
- [x] Redis 7 task distribution (`RPUSH`/`BLPOP`) and fast-path job status caching with 24h TTL.
- [x] Multi-Technical-PDF analysis (`additional_sheets` and `analyzed_items`) in both PDF and Word reports.
- [x] Complete test suite verification (91 unit/integration tests passing, 2 live daemon integration markers).
