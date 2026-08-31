# SEAMTECH Search

Internal search application for SEAMTECH design and technical folders.

The system scans Windows folders, extracts searchable metadata/text, stores it in PostgreSQL for production, and exposes a simple FastAPI web interface for quickly finding references.

## Features

- Recursive folder crawling
- File and folder metadata indexing
- Search by filename, folder name, path, extension, and extracted document text
- PDF text extraction with `pypdf`
- DOCX text extraction with `python-docx`
- PostgreSQL full-text search for production
- SQLite fallback for local tests or quick prototypes
- FastAPI `/search` and `/health` endpoints
- Simple browser interface
- Highlighted search snippets
- File preview for extracted TXT/PDF/DOCX text
- Folder preview plus Open File/Open Folder actions on Windows
- Incremental indexing using file size and modified time
- Batched indexing with progress logs

OCR is not included in the base image. Scanned PDFs and images require an OCR-enabled deployment and should be validated against factory data before being marked content-searchable.

The extractor provides bounded content search for PDF, DOCX, common structured/text formats, OOXML documents such as XLSX/PPTX, and ZIP member manifests. Other files remain metadata-searchable. This is intentionally an explicit support boundary: proprietary CAD and vendor formats require dedicated parsers or licensed SDKs.

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts/bootstrap.py
```

The repository now keeps runtime configuration in the `config/` directory. A ready-to-edit example is available at `config/config.example.json`, and the CLI will use `config/config.json` automatically when it exists.
For real SEAMTECH data, edit `config/config.json` and replace `root_paths` with the Windows shared folders to index.

Example:

```json
{
  "root_paths": [
    "\\\\SERVER\\SEAMTECH\\DesignFiles",
    "D:\\SEAMTECH\\Clients"
  ]
}
```

For production, set `database_url` to PostgreSQL in `config/config.json`:

```json
{
  "database_url": "postgresql://seamtech:${POSTGRES_PASSWORD}@localhost:5432/seamtech_search"
}
```

If `database_url` is empty or missing, the app uses `database_path` as a SQLite fallback for local development.

## Desktop Launcher

Double-click the `SEAMTECH Search` Desktop shortcut, or run:

```powershell
.\SEAMTECH Search.cmd
```

The launcher starts the server in the background, waits for the health endpoint, and opens the browser at `http://127.0.0.1:8000`.

## Index Files

```powershell
python -m seamtech_search index --config config/config.json
```

Force a complete rebuild:

```powershell
python -m seamtech_search index --config config/config.json --rebuild
```

Show database statistics:

```powershell
python -m seamtech_search stats --config config/config.json
```

Test a search from the terminal:

```powershell
python -m seamtech_search search CLIENT-123 --config config/config.json
```

## Run The Web App

```powershell
python -m seamtech_search serve --config config/config.json
```

Open:

```text
http://127.0.0.1:8000
```

## API

```text
GET /health
GET /metrics
GET /search?q=REFERENCE&limit=50&offset=0
GET /preview?path=C:\Path\To\File.pdf
POST /open?path=C:\Path\To\File.pdf
```

Search results include the file/folder name, full path, parent path, extension, size, modified date, folder/file type, match type (`exact_name`, `name`, `path`, or `content`), and a highlighted snippet. The API supports bounded pagination with `limit` and `offset` and returns `has_more`.

`/preview` and `/open` only allow paths inside configured `root_paths`. When `auth_token` is configured, search, preview and open require the `X-SEAMTECH-TOKEN` header. Set `allow_network_access` only when a protected deployment boundary is in place; the token is an interim boundary, not a replacement for enterprise SSO and per-folder authorization.

An indexing run aborts without removing old records if a configured root is unavailable or traversal reports an error. Successful runs record their status and counters in the `scan_runs` table.

## Docker Deployment

Set a strong `POSTGRES_PASSWORD` environment variable and create `config/config.json`, then start PostgreSQL and the web app:

```powershell
$env:POSTGRES_PASSWORD = "use-a-secret-value"
docker compose up --build
```

Run indexing from the host machine when the host has access to the Windows shared folders:

```powershell
.\scripts\run_indexing.ps1 -Config config/config.json
```

Schedule that command in Windows Task Scheduler. For a full service install, point NSSM at:

```text
python -m seamtech_search serve --config config/config.json
```

## Monitoring And Maintenance

- `/health` checks database connectivity, database integrity/size, disk capacity, and index counts.
- `/metrics` reports search request count, error count, last search time, slowest search time, and current health.
- `scripts/run_indexing.ps1` writes a timestamped indexing transcript into `logs/` and exits non-zero on failure, so Task Scheduler can alert on failed runs.

Back up PostgreSQL:

```powershell
.\scripts\backup_postgres.ps1 -DatabaseUrl "postgresql://seamtech:$env:POSTGRES_PASSWORD@localhost:5432/seamtech_search"
```

Restore PostgreSQL:

```powershell
.\scripts\restore_postgres.ps1 -BackupFile data/backups/seamtech-search-YYYYMMDD-HHMMSS.dump -DatabaseUrl "postgresql://seamtech:$env:POSTGRES_PASSWORD@localhost:5432/seamtech_search"
```

Back up or restore the SQLite fallback database:

```powershell
.\scripts\backup_sqlite.ps1
.\scripts\restore_sqlite.ps1 -BackupFile data/backups/search-YYYYMMDD-HHMMSS.db
```

## Tests

```powershell
pytest
```

## Project Structure

- `config/`: runtime and example configuration files
- `data/`: generated runtime data and local SQLite fallback index
- `logs/`: application logs
- `sample_data/`: sample content for local development
- `scripts/`: setup and maintenance helpers
- `seamtech_search/`: Python package containing the crawler, indexer, API and CLI
- `tests/`: regression tests

## Recommended Deployment

Run this on one internal server or VM that can access the shared Windows folders. Use PostgreSQL as the production index store and schedule indexing with Task Scheduler or NSSM.
