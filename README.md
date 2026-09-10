# SEAMTECH Search

[![CI](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/workflows/ci.yml/badge.svg)](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.116.1-009688.svg)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js-16.3.3-black.svg)](https://nextjs.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791.svg)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-7.0-DC382D.svg)](https://redis.io/)
[![MinIO / S3](https://img.shields.io/badge/Storage-S3%20%2F%20MinIO%20%2F%20R2-orange.svg)](https://min.io/)

Internal search engine, technical reference folder ingestion platform, and manufacturing dossier analysis engine for SEAMTECH design and fabrication files.

For the comprehensive technical and operational report, see [docs/PROJECT_REPORT.md](docs/PROJECT_REPORT.md).

---

## 🏗️ Cloud-Native Architecture (Decoupled Storage & Compute)

The system is designed as a **stateless, resilient, cloud-native architecture**:

```
                       ┌────────────────────────────┐
                       │    Web Browser / Client    │
                       └──────────────┬─────────────┘
                                      │ (HTTPS)
                                      ▼
                       ┌────────────────────────────┐
                       │    Next.js 16 Frontend     │
                       │  (Authenticated API Proxy) │
                       └──────────────┬─────────────┘
                                      │ (Internal HTTP)
                                      ▼
                       ┌────────────────────────────┐
                       │    FastAPI API Gateway     │
                       │  - Rate Limiter (Sliding)  │
                       │  - Probes & Audit Trails   │
                       └──────┬──────────────┬──────┘
                              │              │
         ┌────────────────────┘              └────────────────────┐
         ▼                                                        ▼
┌────────────────────────────┐                              ┌────────────────────────────┐
│      Redis 7 Cluster       │                              │       PostgreSQL 16        │
│ - Task Queue (RPUSH/BLPOP) │                              │ - Full-Text Search (GIN)   │
│ - Fast-Path Job Cache      │                              │ - JSONB Document Payloads  │
│ - Sliding-Window Limiter   │                              │ - Connection Pooling       │
└─────────────┬──────────────┘                              │ - Append-Only Audit Log    │
              │                                             └────────────────────────────┘
              ▼                                                           ▲
┌────────────────────────────┐                                            │
│  Pipeline Worker Daemon    │────────────────────────────────────────────┘
│  - Multi-Sheet Extraction  │
│  - Dual PDF/DOCX Reports   │
│  - Scratch Staging Purge   │
└─────────────┬──────────────┘
              │
              ▼ (S3 API Upload / Presigned URLs)
┌───────────────────────────────────────────────────────────┐
│     Object Storage (MinIO / Cloudflare R2 / AWS S3)       │
│     - Raw Technical PDFs & Excel Sheets                   │
│     - Generated PDF & Word DOCX Synthesis Reports         │
└───────────────────────────────────────────────────────────┘
```

- **Object Storage (MinIO / Cloudflare R2 / AWS S3):** Stores all permanent assets (PDF drawings, Excel workbooks, and generated synthesis reports). Download links are provided via time-limited presigned URLs.
- **Task Queue & Cache (Redis 7):** Handles asynchronous job dispatching, fast-path job status caching (24h TTL), and distributed sliding-window rate limiting.
- **Database (PostgreSQL 16):** Stores document indexes, full-text search vectors (`tsvector` with GIN indexing), dynamic fabrication metadata in `JSONB`, and append-only audit logs.
- **Stateless Host/VPS:** Scratch directories are purged immediately upon upload to object storage. If the container or host restarts, no customer data is lost.

---

## ✨ Features

- **Multi-Technical-PDF Dossier Analysis:** Automatically analyzes and extracts dimensions and technical specifications across **all** secondary technical sheets within a folder.
- **Twin PDF & Excel BOM Parsing:** Layout-aware extraction using `pdfplumber` and tabular BOM component extraction using `openpyxl`.
- **Dual Synthesis Reports:** Automatically produces synchronized, styled **PDF** (`reportlab`) and editable **Word DOCX** (`python-docx`) summary reports.
- **S3 / MinIO / Cloudflare R2 Storage:** S3-compatible object storage with automatic bucket provisioning and presigned URL streaming.
- **Asynchronous Pipeline & Worker:** Fast `POST /imports` (HTTP 202 Accepted) with background task queues and cooperative cancellation (`POST /imports/{id}/cancel`).
- **Distributed Sliding-Window Rate Limiting:** Enforces 600 req/min limits with automatic HTTP 429 and `Retry-After` headers.
- **Append-Only Audit Logging:** Immutable audit trail (`GET /audit`) with SHA-256 token fingerprinting.
- **Health Probes:** Production-ready container endpoints (`/live`, `/ready`, `/health`, `/metrics`).
- **Security & TLS Guard:** Enforces TLS reverse proxying for non-local network bindings.

---

## 🚀 Quickstart with Docker Compose

To start the complete stack locally (PostgreSQL 16, MinIO S3, Redis 7, FastAPI Backend, and Next.js Frontend):

```bash
# 1. Clone the repository and configure environment variables
cp .env.example .env

# 2. Launch the entire containerized stack
docker compose up -d
```

### Services & Web Consoles

| Service | Host URL | Credentials |
|---|---|---|
| **Next.js Frontend** | `http://localhost:3000` | Authenticated via backend |
| **FastAPI Backend** | `http://localhost:8000` | API Docs at `/docs` |
| **MinIO Web Console** | `http://localhost:9001` | User: `minioadmin` / Pass: `minioadmin` |
| **MinIO S3 Endpoint**| `http://localhost:9000` | S3 API endpoint |
| **PostgreSQL 16** | `localhost:5433` | User: `seamtech` / DB: `seamtech_search` |
| **Redis 7** | `localhost:6379` | Standard Redis port |

---

## ⚙️ Configuration Reference

Configuration can be supplied via `config/config.json` or environment variables:

| Setting | Environment Variable | Default | Description |
|---|---|---|---|
| `database_url` | `SEAMTECH_DATABASE_URL` | `postgresql://...` | PostgreSQL connection string |
| `storage_backend` | `SEAMTECH_STORAGE_BACKEND` | `s3` | Storage backend (`s3` or `local`) |
| `s3_endpoint_url` | `SEAMTECH_S3_ENDPOINT_URL` | `http://127.0.0.1:9000` | S3 API endpoint URL (MinIO / R2 / AWS) |
| `s3_bucket` | `SEAMTECH_S3_BUCKET` | `seamtech-documents` | S3 bucket name |
| `s3_access_key` | `SEAMTECH_S3_ACCESS_KEY` | `minioadmin` | S3 Access Key / Token ID |
| `s3_secret_key` | `SEAMTECH_S3_SECRET_KEY` | `minioadmin` | S3 Secret Access Key |
| `redis_url` | `SEAMTECH_REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis connection URL |
| `auth_token` | `SEAMTECH_AUTH_TOKEN` | `""` | Shared API token |
| `behind_tls_proxy` | `SEAMTECH_BEHIND_TLS_PROXY` | `false` | Enables network binding behind TLS proxy |
| `rate_limit_per_minute` | `SEAMTECH_RATE_LIMIT_PER_MINUTE` | `600` | Max requests per minute per IP |
| `min_free_bytes` | `SEAMTECH_MIN_FREE_BYTES` | `1073741824` | 1 GB disk floor for staging safety |

---

## 🛠️ API Reference Summary

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/live` | Liveness probe (200 OK) |
| `GET` | `/ready` | Readiness probe (verifies PostgreSQL and Redis) |
| `GET` | `/health` | Detailed system, database, and storage metrics |
| `GET` | `/metrics` | Request timings and error rates |
| `GET` | `/search?q={query}` | Full-text search with highlighted snippets |
| `POST` | `/imports/scan` | Two-phase scan for candidate files in a folder |
| `POST` | `/imports/confirm` | Confirm candidate selection and run extraction |
| `POST` | `/imports` | Asynchronous import (returns HTTP 202 Accepted) |
| `GET` | `/imports/{id}` | Poll import job status and progress |
| `POST` | `/imports/{id}/cancel` | Cooperatively cancel a running import job |
| `PATCH` | `/imports/{id}` | Update parameters, regenerate reports, and re-upload |
| `POST` | `/imports/{id}/retry-upload`| Retry failed upload to S3/OneDrive |
| `GET` | `/audit` | Query append-only audit trail |
| `POST` | `/maintenance/cleanup` | Execute storage retention cleanup |

---

## 🧪 Testing & Verification

Run the full automated test suite:

```bash
# Run all unit and integration tests
pytest

# Run linter
ruff check .
```

To run end-to-end tests:

```bash
cd frontend
pnpm exec playwright test
```

---

## 📚 Documentation

Detailed technical guides and operational specifications are available in `docs/`:
- **[PROJECT_REPORT.md](docs/PROJECT_REPORT.md):** Complete technical, architectural, and business report.
- **[REPORT.md](docs/REPORT.md):** Production readiness report and deployment runbook.
- **[STRUCTURE.md](docs/STRUCTURE.md):** Repository structure and component layout.
- **[IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md):** Architecture roadmap and milestone status.
- **[TLS.md](docs/TLS.md):** Production TLS reverse-proxy hardening guide.

---

## 📄 License

Internal Proprietary — SEAMTECH. All Rights Reserved.
