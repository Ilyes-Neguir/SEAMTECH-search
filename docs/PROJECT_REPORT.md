# SEAMTECH Search
## Comprehensive Technical & Architectural Project Report

**Project Type:** Cloud-Native Technical File Ingestion, Metadata Extraction, Full-Text Search, and Dossier Analysis Platform  
**Version:** 0.4.0  
**Target Environment:** Linux VPS / Docker / Cloudflare R2 / AWS S3 / Windows Workstations  
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

The codebase adheres to rigorous testing standards across multiple layers:

```text
============================= test session starts ==============================
platform linux -- Python 3.11.2, pytest-8.4.1
collected 93 items

tests/test_api.py ..................................................... [ 10%]
tests/test_config.py .................................................. [ 20%]
tests/test_extractors.py .............................................. [ 30%]
tests/test_import_pipeline.py ......................................... [ 40%]
tests/test_import_workflow.py ......................................... [ 55%]
tests/test_indexer.py ................................................. [ 65%]
tests/test_jobs_api.py ................................................ [ 75%]
tests/test_onedrive.py ................................................ [ 80%]
tests/test_pooling.py ................................................. [ 85%]
tests/test_redis.py ................................................... [ 90%]
tests/test_scan_safety.py ............................................. [ 95%]
tests/test_storage.py ................................................. [100%]

=================== 91 passed, 2 skipped (Postgres/S3 live) in 5.4s ============
```

### Verification Matrix:
- **Unit & Pipeline Tests:** 91 passing tests verifying S3 client, Redis queue, rate limiting, connection pooling, multi-sheet PDF extraction, ReportLab PDF generation, python-docx Word report generation, and retention cleanup.
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

SEAMTECH Search v0.4.0 delivers a modern, robust, and scalable platform that transitions engineering file search and manufacturing dossier analysis into a truly cloud-native paradigm. With its decoupled architecture, durable S3 storage, resilient Redis queues, and multi-sheet technical extraction, the platform provides complete data safety, fast response times, and an intuitive user experience for engineering and workshop teams.
