# SEAMTECH Search
## Comprehensive Technical & Architectural Project Report

**Project Type:** Cloud-Native Technical File Ingestion, Metadata Extraction, Full-Text Search, and Dossier Analysis Platform  
**Version:** 0.4.0  
**Target Environment:** Linux VPS / Docker / MinIO (production actuelle) / R2 éventuel après décision D-2 / poste Windows atelier
**Repository:** `Ilyes-Neguir/SEAMTECH-search`  
**Date:** September 2026  

---

## 1. Executive Summary

**SEAMTECH Search** is an enterprise-grade document search, reference ingestion, and technical fabrication dossier analysis engine designed specifically for technical manufacturing, sailmaking, marine engineering, and industrial design teams.

The platform solves the challenge of discovering, validating, and synthesizing unstructured engineering archives (technical drawings, dimensioned specifications, Bill-of-Materials spreadsheets, and client reference folders) into structured, queryable data and synchronized executive reports.

Originally conceived as a local desktop indexer, the application has evolved into a **fully decoupled, cloud-native, stateless architecture** powered by:
- **FastAPI (Python 3.11+)** for the high-throughput REST API backend.
- **Next.js 16 (React 19 & Tailwind CSS 4)** for the modern web interface.
- **S3-Compatible Object Storage (MinIO, Cloudflare R2, AWS S3)** for durable storage of raw assets and generated reports.
- **Redis 7** for distributed task queues, sliding-window rate limiting, and progress caching.
- **PostgreSQL 16** with JSONB storage and `tsvector`/GIN indexing for high-speed full-text search.
- **Multi-Technical-PDF Analyzer** with `pdfplumber` layout-aware parsing and `openpyxl` table detection.
- **Dual-Report Generation Engine** producing synchronized **PDF** (`reportlab`) and **Word DOCX** (`python-docx`) synthesis summaries.

---

## 2. Business Problem & Objectives

### 2.1 The Operational Challenge
Technical fabrication shops face severe information fragmentation:
1. **Scattered Archives:** Client project directories contain heterogeneous mixes of CAD exports, technical specification PDFs, Excel nomenclatures (BOMs), and raw cutting notes across network shares.
2. **Multi-Sheet Technical Dossiers:** A single client folder often contains multiple technical PDF sheets (e.g., mainsail spec, genoa spec, spinnaker plan) and multi-tab Excel workbooks. Traditional crawlers either indexed only the first PDF or failed to extract structured manufacturing dimensions.
3. **Data Loss & Stateless VPS Constraints:** Storing customer production files directly on ephemeral VPS disks risks permanent data loss on container rebuilds or host reprovisioning.
4. **Manual Synthesis Burden:** Engineers spent hours transcribing dimensions (length, width, area, materials, fabric weights) into synthesis reports for the workshop floor.

### 2.2 Core Objectives
- **Zero Local Disk Retention:** Stateless execution where ephemeral scratch directories are purged immediately after uploading assets to S3/MinIO/R2.
- **Full Multi-Sheet Technical Extraction:** Extract dimensions, materials, BOM tables, and fabrication notes across all technical sheets within a folder.
- **Asynchronous Execution & Cooperative Cancellation:** Fast HTTP 202 submission with Redis worker queues, live progress polling, and cooperative job cancellation.
- **Robust Security & Auditing:** Sliding-window rate limiting, token fingerprinting in append-only audit logs, and non-local TLS enforcement.
- **Full Operational Resiliency:** Automated health probes (`/live`, `/ready`, `/health`, `/metrics`), graceful recovery of orphaned jobs on startup, and pre-flight disk guards.

---

## 3. Decoupled Cloud-Native Architecture

The platform architecture is strictly decoupled between presentation, compute, messaging, search metadata, and object storage:

```
                                  ┌───────────────────────────┐
                                  │   Web Browser / Client    │
                                  └─────────────┬─────────────┘
                                                │ (HTTPS)
                                                ▼
                                  ┌───────────────────────────┐
                                  │    Next.js 16 Frontend    │
                                  │ (Server-Side Proxy Auth)  │
                                  └─────────────┬─────────────┘
                                                │ (Internal HTTP)
                                                ▼
                                  ┌───────────────────────────┐
                                  │    FastAPI API Gateway    │
                                  │  - Rate Limiter (Sliding) │
                                  │  - Auth & Security Guard  │
                                  │  - Probes & Audit Trails  │
                                  └──────┬─────────────┬──────┘
                                         │             │
                ┌────────────────────────┘             └────────────────────────┐
                ▼                                                               ▼
  ┌───────────────────────────┐                                   ┌───────────────────────────┐
  │      Redis 7 Cluster      │                                   │       PostgreSQL 16       │
  │ - Async Task Queue (List) │                                   │ - Full-Text Search (GIN)  │
  │ - Fast-Path Status Cache  │                                   │ - JSONB Document Payloads │
  │ - Sliding-Window Limiter  │                                   │ - Connection Pooling      │
  └─────────────┬─────────────┘                                   │ - Append-Only Audit Log   │
                │                                                 └───────────────────────────┘
                ▼                                                               ▲
  ┌───────────────────────────┐                                                 │
  │ Dedicated Pipeline Worker │                                                 │
  │ - pdfplumber & openpyxl   │─────────────────────────────────────────────────┘
  │ - Dual PDF/DOCX Synthesis │
  │ - Ephemeral Scratch Purge │
  └─────────────┬─────────────┘
                │
                ▼ (S3 API Upload / Presigned URLs)
  ┌───────────────────────────────────────────────────────────┐
  │   Object Storage (MinIO / Cloudflare R2 / AWS S3)         │
  │   - raw-uploads/{folder}/fiche-technique-*.pdf            │
  │   - raw-uploads/{folder}/nomenclature-*.xlsx              │
  │   - reports/{folder}/technical-report.pdf                 │
  │   - reports/{folder}/technical-report.docx                │
  └───────────────────────────────────────────────────────────┘
```

---

## 4. Component Deep Dive

### 4.1 Storage Layer (`seamtech_search/storage.py`)
- **Multi-Backend S3 Client:** Compatible with local MinIO, Cloudflare R2, AWS S3, and Wasabi using `boto3`.
- **Automatic Bucket Lifecycle:** Automatically creates buckets on initialization if not present.
- **Presigned URL Streaming:** Generates secure, time-limited presigned download URLs for raw files and generated reports so the API server does not bottleneck large file transfers.
- **Complete Dossier Upload:** Handles uploading the complete dossier (all raw technical PDFs, Excel sheets, and both generated PDF/DOCX reports) with standardized key prefixes.

### 4.2 Messaging & Task Queue (`seamtech_search/redis_store.py` & `worker.py`)
- **Queue Mechanism:** Utilizes Redis `RPUSH` and blocking `BLPOP` for robust FIFO job dispatching.
- **Fast-Path Status Cache:** Caches job state and progress in Redis keys (`seamtech:job:{id}`) with 24-hour TTL, eliminating PostgreSQL query load during frontend progress polling.
- **Distributed Sliding-Window Rate Limiting:** Enforces rate limits (default 600 req/min) using Redis sorted sets (`ZADD`, `ZREMRANGEBYSCORE`, `ZCARD`), accurately metering distributed requests with HTTP 429 and `Retry-After` headers.
- **Dedicated Worker Loop:** Runs as an isolated background task, popping jobs, executing imports, saving results to PostgreSQL, uploading artifacts to S3, and purging local scratch files.

### 4.3 Database & Indexing Engine (`seamtech_search/indexer.py`)
- **PostgreSQL 16 Primary:** Uses PostgreSQL's `tsvector` with French/English stemming and GIN index for sub-millisecond query evaluation.
- **Connection Pooling:** `ThreadedConnectionPool` ensures safe concurrent database access with per-connection statement timeouts (`statement_timeout_ms`).
- **Flexible Document Schema:** Stores raw extraction metadata in PostgreSQL `JSONB` columns, enabling ad-hoc queries across dynamic sail parameters and BOM item lists.
- **SQLite Fallback:** Preserves lightweight SQLite FTS5 fallback mode for standalone local testing and development.

### 4.4 Extraction & Multi-Sheet Analysis (`seamtech_search/extractors.py` & `import_pipeline.py`)
- **Two-Phase Anchor Detection:** Scans files against standardized technical anchors (`surface`, `guindant`, `bordure`, `chute`, `longueur`, `largeur`, `matériau`, `grammage`, `finition`, `mesures dessin`).
- **Multi-Sheet Technical Analysis:** When a dossier contains multiple technical PDF sheets, every sheet is extracted, analyzed, and stored in `additional_sheets` / `analyzed_items`.
- **Dimension Normalization:** Automatically standardizes millimeters, meters, centimeters, and square meters into normalized numeric values (`longueur_mm`, `largeur_mm`, `surface_m2`).
- **BOM Parsing:** Parses tabular data from Excel worksheets using `openpyxl`, categorizing components (cloth, webbing, rings, battens, thread) with quantities and unit measures.

### 4.5 Dual-Format Report Generator
Every imported folder or dossier automatically generates two synchronized professional synthesis reports:
1. **PDF Report (`reportlab`):** Styled with SEAMTECH corporate branding, summary headers, dimension tables, secondary sheet breakdowns, and bill-of-materials tables.
2. **Word Report (`python-docx`):** Fully editable `.docx` document formatted with clean tables and typography for engineering annotations.

### 4.6 Next.js 16 Web Interface (`frontend/`)
- **Server-Side API Proxy:** Browser requests go exclusively to Next.js API route handlers (`frontend/app/api/*`), which inject authorization tokens and proxy to the FastAPI backend. No backend credentials or internal URLs are exposed to the client.
- **Two-Phase Import UI:** Supports drag-and-drop file uploads, candidate preview with anchor confidence indicators, editable correction forms, live progress bar polling, and download links for S3-stored PDF/Word reports.
- **Search & Highlighting:** Real-time search with highlighted snippets, category badges, preview drawer, and clipboard copy actions.

---

## 5. Security, Auditing & Operational Safety

### 5.1 Append-Only Audit Logging (`seamtech_search/audit.py`)
- Every mutating action (folder scan, import creation, manual correction, re-upload, maintenance cleanup) and search query is recorded in the `audit_log` table.
- **Credential Protection:** Tokens are cryptographically fingerprinted using SHA-256 prefixes (`token:...`). Raw passwords and bearer tokens are never logged.

### 5.2 TLS & Network Binding Rules
- Non-local host bindings (e.g. `0.0.0.0` or LAN IPs) require `behind_tls_proxy=true` when authentication is enabled, preventing plaintext transmission of credentials across local networks.
- Comprehensive reverse-proxy deployment guide documented in `docs/TLS.md`.

### 5.3 Retention & Storage Safeguards (`seamtech_search/retention.py`)
- Configurable retention periods: Generated reports (default 90 days), staged uploads (default 7 days), audit records (default 365 days).
- **Pre-Flight Disk Floor:** Verifies available disk bytes (`min_free_bytes`, default 1 GB) before accepting new uploads, returning HTTP 507 Insufficient Storage if storage thresholds are breached.

---

## 6. Verification, Testing & Quality Assurance

The codebase adheres to rigorous testing standards across multiple layers. The
full suite runs on every push (`.github/workflows/ci.yml`): the SQLite and
live-Postgres test selections, a per-module coverage gate
(`scripts/coverage_gate.py`), `ruff check .`, `pip-audit`, the Docker image
builds, the documented `docker compose` deployment, and the Playwright E2E
suite. (An earlier version of this document pasted a raw pytest transcript;
transcripts go stale, so CI is the source of truth.)

### Verification Matrix:
- **Unit & Pipeline Tests:** automated suite verifying S3 client, Redis queue, rate limiting, connection pooling, multi-sheet PDF extraction, ReportLab PDF generation, python-docx Word report generation, accent parity across SQLite/Postgres, and retention cleanup (count and result in CI, not hardcoded here).
- **Static Analysis & Linting:** `ruff check .` with 0 warnings or errors across the entire codebase.
- **End-to-End Testing (Playwright):** Automated tests in `frontend/e2e/` verifying search workflow, folder scanning, file upload, and report viewing.
- **Type Safety:** Full TypeScript strict checking (`tsc --noEmit`) and Pydantic v2 data models.

---

## 7. Operational Runbook & Production Deployment

### 7.1 Docker Compose Deployment
Launch the complete stack (PostgreSQL, MinIO, Redis, Backend, Frontend) in one command:

```bash
# 1. Prepare environment
cp .env.example .env

# 2. Start services
docker compose up -d
```

### 7.2 Service Ports & Endpoints
| Service | Internal Port | Host Port | Purpose |
|---|---|---|---|
| PostgreSQL 16 | 5432 | 5433 | Relational database & full-text search |
| MinIO S3 API | 9000 | 9000 | S3-compatible document object storage |
| MinIO Console | 9001 | 9001 | Web console for bucket management |
| Redis 7 | 6379 | 6379 | Task queue & sliding-window rate limiter |
| FastAPI Backend | 8000 | 8000 | REST API, worker coordinator, health probes |
| Next.js Frontend | 3000 | 3000 | Web UI & authenticated reverse proxy |

### 7.3 Health Probes & Monitoring
- `GET /live`: Kubernetes/Docker liveness probe.
- `GET /ready`: Readiness probe verifying PostgreSQL and Redis connections.
- `GET /health`: Detailed system health, database metrics, storage status, and index statistics.
- `GET /metrics`: Latency statistics, query throughput, and error counters.

---

## 8. Conclusion

SEAMTECH Search v0.4.0 delivers a modern, robust, and scalable platform that transitions engineering file search and manufacturing dossier analysis into a truly cloud-native paradigm. With its decoupled architecture, durable object storage, resilient Redis queues, and multi-sheet technical extraction, the platform provides a strong technical baseline. Production readiness remains conditional on the explicit human and infrastructure gates below; CI and synthetic measurements are not production evidence.

---

## 9. Production-readiness closeout — 2026-09-29

### 9.1 State, scope, and verdict

- **Branch:** `arena/01a0ed7b-seamtech-search`
- **Final branch HEAD (last confirmed):** `156ab883269553dac2d02febd0622b9460677a72` (release portal, captured benchmark outputs, and CI closeout).
- **Validated candidate code SHA:** `984e2dbb3379aa0080d41bdca9f2d9dae673c992`; subsequent commits `01484d0` and `156ab88` update documentation only, not application code. Push and PR CI also passed on `156ab88`.
- **PR:** #33, open and not merged at last confirmed state.
- **Verdict:** **NOT CLEARED FOR PRODUCTION / NO MERGE AUTHORIZED.** Lots 1–3 code, local checks, final push/PR CI and the synthetic benchmark are complete. The human and real-environment acceptance gates remain outstanding. MinIO remains the production object-store backend; R2 is only an option pending D-2.

### 9.2 Change inventory (base `a7ffe7d2800ed697f306a93b0564b4ad8c87eb74` → code HEAD)

32 files changed; 1,758 insertions and 119 deletions, including the synthetic raw benchmark outputs and removal of the duplicate root PDF:

| File(s) | Change |
|---|---|
| `.github/workflows/ci.yml` | Ubuntu 24.04 runners, verified action versions, CI/dependency-policy updates. |
| `.github/workflows/scale-bench.yml` | Bounded optional 10k synthetic PostgreSQL benchmark, targeted PR trigger, diagnostics and artifact upload. |
| `7792-SO_ffab.pdf` | Removed duplicate root copy; canonical fixture retained. |
| `scripts/audit_dependency_policy.py` | Runtime pip / production pnpm audit policy. |
| `scripts/scale_bench.py` | Synthetic data seed, endpoint percentiles and EXPLAIN. |
| `docs/benchmarks/scale-bench-synthetique-36594937291.json`, `.md` | Reconstructed synthetic result JSON and complete top-3 EXPLAIN output from this run's compressed GitHub annotations. |
| `seamtech_search/api.py`, `seamtech_search/fiches/routes.py` | Optional assistant/ML flag and route behavior. |
| `frontend/app/recherche/page.tsx`, `frontend/components/recherche-fiches-app.tsx`, `frontend/lib/fiche.ts` | Search feature-flag/UI wiring. |
| `frontend/components/validation-app.tsx`, `frontend/components/pdf-viewer.tsx`, `frontend/e2e/validation.spec.ts` | Human validation workflow, keyboard and PDF highlighting, anomaly confirmation, session timer, E2E coverage. |
| `tests/test_fonctionnalites_optionnelles.py` | Default-enabled and disabled optional route coverage. |
| `tests/test_validation_workflow.py` | Group-validation and workflow coverage adjustments. |
| `tests/test_confidentialite_depots.py`, `tests/test_empreintes_fixtures.py`, `tests/test_garde_fous_preparation.py`, `tests/test_ocr_etages.py` | Duplicate-PDF/privacy/OCR and source-read-only safeguards. |
| `.env.example`, `docker-compose.yml` | Optional feature configuration. |
| `docs/OPTIONAL_FEATURES.md` | Feature flag usage and compatibility. |
| `docs/deploiement/vps01-r2.md`, `docs/deploiement/poste-atelier.md` | VPS01/R2 option and workshop runbooks. |
| `docs/RELEASE_CANDIDATE_CHECKLIST.md` | Candidate controls and dated release portal, including unclosed gates. |
| `docs/verite_terrain/EMPREINTES.md` | PDF duplicate removal note and hash. |
| `docs/verite_terrain/FUSION_MAIN.md`, `docs/verite_terrain/MESURE_VALIDATION_2MIN.md` | State notes and human-measurement gate. |
| `CHANGELOG.md` | Lot 1–3 change record. |

The root PDF and canonical file SHA-256 matched exactly: `43afc51e55ae598d3eaffc3096f0e7ddaa00e8ddc579ae315bb31b4dbf1c1f40`. The root blob remains in Git history. The seven corpus ZIPs were re-hashed on 2026-09-29; the outputs are recorded in `docs/RELEASE_CANDIDATE_CHECKLIST.md` §19 and their working-tree bytes were not changed.

### 9.3 Validation evidence

- Local selected suite (markers only): **766 passed, 3 skipped, 234 deselected**. Earlier baseline reference was approximately 746 passed / 3 skipped; this is not a same-commit before/after comparison.
- Previously successful on the Lot 3 tree: `ruff check .`; Python compilation; YAML parsing; `git diff --check`; frontend TypeScript and production build; focused optional-feature/selection/privacy tests (**32 passed**).
- Benchmark **SYNTHÉTIQUE**: [run 36594937291](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36594937291), 10,000 synthetic records, 50 samples per scenario, successful in **1 min 5 s**. Metrics and all three complete EXPLAIN ANALYZE/BUFFERS plans were reconstructed from compressed GitHub check annotations and saved in `docs/benchmarks/scale-bench-synthetique-36594937291.{json,md}`.

  | Scenario | p50 ms | p95 ms | p99 ms | max ms |
  |---|---:|---:|---:|---:|
  | mot_simple | 42.59 | 48.11 | 48.26 | 48.26 |
  | multi_mots | 52.83 | 66.45 | 86.05 | 86.05 |
  | code | 6.70 | 6.83 | 15.48 | 15.48 |
  | facette_dimension | 50.71 | 62.05 | 129.36 | 129.36 |
  | filtres | 53.44 | 80.11 | 150.62 | 150.62 |
  | suggestions | 4.28 | 4.58 | 4.67 | 4.67 |

  All six synthetic p95 values are below the benchmark's **indicative 250 ms** target. This does not establish the separate product latency target or VPS01/R2 performance. The first benchmark attempt failed because direct script execution omitted the repository root from `sys.path` (`ModuleNotFoundError: seamtech_search`); `PYTHONPATH=.` corrected it.
- On candidate SHA `984e2db`, push CI [36594929912](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36594929912), PR CI [36594937282](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36594937282), and synthetic run [36594937291](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36594937291) all passed. After the docs-only closeout commit `01484d0`, push CI [36595978800](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36595978800) and PR CI [36595984492](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36595984492) also passed; repeat synthetic run [36595985033](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36595985033) passed. On final recorded SHA `156ab88`, push CI [36596936501](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36596936501) and PR CI [36596943586](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36596943586) each passed **11/11 jobs**. The jobs include live E2E validation, duplicate banner, named-user auth, Docker, integration, backend Python 3.11/3.12/3.13, security, corpus recipe, OCR and backup. The earlier E2E failure was a test-state mistake (pressing `r` while a text field retained focus); the test now blurs the input before invoking page-level shortcuts.
- Repeat benchmark [36596943828](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/36596943828) also passed on `156ab88` (10,000 synthetic records; 50 samples/scenario; 1 min 13 s). Its job annotation reported the following **SYNTHÉTIQUE** values (milliseconds):

  | Scenario | p50 | p95 | p99 | max |
  |---|---:|---:|---:|---:|
  | mot_simple | 67.25 | 72.99 | 83.62 | 83.62 |
  | multi_mots | 100.77 | 118.04 | 140.22 | 140.22 |
  | code | 11.56 | 23.32 | 26.09 | 26.09 |
  | facette_dimension | 79.74 | 86.74 | 87.95 | 87.95 |
  | filtres | 82.74 | 84.17 | 98.89 | 98.89 |
  | suggestions | 7.48 | 7.64 | 7.85 | 7.85 |

  All six p95 values are below this benchmark's indicative 250 ms threshold only. This does not establish live VPS01/R2 performance or user-facing latency. Full EXPLAIN plans are archived from run `36594937291` in `docs/benchmarks/`; the repeat run's own EXPLAIN annotations are available in its check details.

### 9.4 Explicitly unmeasured / remaining gates

- Human review of references REF-001…REF-007, including corrections and accept/reject decisions: **NON MESURÉ / OPERATOR ACTION REQUIRED**.
- Real VPS01 scale, deployment, live response latency, restore/rollback, and workshop/Windows operation: **NON MESURÉ**.
- R2 acceptance and production switch: **DECISION D-2 REQUIRED**; MinIO stays production meanwhile.
- Calibrated confidence thresholds and grouped validation: locked at `calibre:false`; HTTP 409 remains expected until calibration is justified by human-validated real records.
- Other outstanding storage decisions (including D-4 credentials) remain in the storage audit/runbooks.

**Release rule:** no PR merge or production deployment without explicit sponsor decision, final green CI, operator validation of REF-001…007, and completed real-environment backup/restore/rollback checks. The synthetic job is a bounded engineering benchmark, not an acceptance test for production.
