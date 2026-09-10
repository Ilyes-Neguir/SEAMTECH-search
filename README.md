# SEAMTECH Search

Internal search and reference-folder ingestion application for SEAMTECH design and technical fabrication files.

For the complete academic and technical presentation, see [docs/PROJECT_REPORT.md](docs/PROJECT_REPORT.md).

## Cloud-Native Architecture (Decoupled Storage & Compute)

The architecture is designed to be **stateless and resilient**:

```
[User Browser]
      │
      ▼ (Uploads / Scans Reference: PDF + Excel)
[FastAPI Server & Next.js on VPS01]
      │
      ├───► 1. Background Jobs & Rate Limiting via [Redis]
      ├───► 2. Raw Files & Generated Reports (PDF + Word) stored in [MinIO / Cloudflare R2 / AWS S3]
      └───► 3. Search Index, BOM Metadata, and FTS stored in [PostgreSQL]
```

- **Object Storage (MinIO locally / Cloudflare R2 or AWS S3 in prod)**: Houses all raw technical drawings, Excel workbooks, and generated PDF/Word reports with presigned URL streaming.
- **Task Queue & Cache (Redis)**: Handles distributed background job tickets, progress caching, and sliding-window rate limiting.
- **Database (PostgreSQL)**: Stores document indexes, extracted fabrication metadata (dimensions, materials, quantities, bill of materials), and append-only audit logs.
- **Stateless Host/VPS**: No permanent customer files reside on the VPS disk. If VPS01 crashes or is reprovisioned, all documents and metadata remain 100% safe.

---

## Features

- **Recursive folder crawling** & metadata indexing for engineering files.
- **Twin PDF & Excel BOM Extraction**: Extracts technical sail parameters and Excel Bill-of-Materials tables (`openpyxl`).
- **Dual-Format Report Generation**: Produces styled technical reports in both **PDF** (`reportlab`) and **Word DOCX** (`python-docx`).
- **S3 / MinIO / Cloudflare R2 Integration**: S3-compatible object storage for all imported artifacts with zero local disk retention.
- **Redis Queue & Rate Limiter**: Distributed sliding-window rate limiting and async job coordination.
- **PostgreSQL Full-Text Search**: Production-grade search vectors, indexing, and connection pooling.
- **Async Import Pipeline**: `POST /imports` (HTTP 202 Accepted) with progress polling and cooperative cancellation (`POST /imports/{id}/cancel`).
- **Audit Logging**: Secure audit trail (`GET /audit`) with actor token fingerprinting.
- **Health Probes**: Container-ready `/live`, `/ready`, and `/health` endpoints.

---

## Local Development with Docker (MinIO + PostgreSQL + Redis)

Run the full cloud-native stack locally in Docker:

```powershell
Copy-Item .env.example .env
# Start PostgreSQL (5433), MinIO S3 (9000 API, 9001 Web Console), and Redis (6379)
docker compose up -d postgres minio redis
```

- **MinIO Console**: `http://localhost:9001` (User: `minioadmin` / Password: `minioadmin`)
- **MinIO S3 Endpoint**: `http://localhost:9000`
- **PostgreSQL**: `localhost:5433` (DB: `seamtech_search`)
- **Redis**: `localhost:6379`

### Environment Configuration

Configure `config/config.json` or pass environment variables:

```json
{
  "database_url": "postgresql://seamtech:CHANGE_ME@127.0.0.1:5433/seamtech_search",
  "storage_backend": "s3",
  "s3_endpoint_url": "http://127.0.0.1:9000",
  "s3_bucket": "seamtech-documents",
  "s3_access_key": "minioadmin",
  "s3_secret_key": "minioadmin",
  "redis_url": "redis://127.0.0.1:6379/0"
}
```

---

## Local Setup (Native Python)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts/bootstrap.py
```

Run tests:

```powershell
pytest
```

Run FastAPI Backend:

```powershell
python -m seamtech_search serve --config config/config.json
```

Run Next.js Frontend:

```powershell
cd frontend
npm run dev
```

---

## Production Deployment (Cloudflare R2 + PostgreSQL + Redis on VPS)

When deploying to production VPS:
1. Point `SEAMTECH_S3_ENDPOINT_URL` to your Cloudflare R2 endpoint: `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`.
2. Set `SEAMTECH_S3_ACCESS_KEY` and `SEAMTECH_S3_SECRET_KEY` from Cloudflare R2 API Tokens.
3. Set `SEAMTECH_DATABASE_URL` to your PostgreSQL database.
4. Set `SEAMTECH_REDIS_URL` to your Redis container or managed Redis.
5. Launch via `docker compose up --build -d`.
