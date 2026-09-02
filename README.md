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
- Incremental indexing: unchanged files (same size, modified time, and extractor version) are never re-extracted, only re-stat'd
- Content re-extraction is version-gated: bumping `CURRENT_EXTRACTOR_VERSION` in `extractors.py` forces every file to be re-parsed on the next scan even if nothing changed on disk, so a parser fix actually reaches the index
- Per-file extraction timeout: a hung or pathological file is marked `[extraction timed out]` instead of stalling the whole scan
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

The repository now keeps runtime configuration in the `config/` directory. A ready-to-edit PostgreSQL example is available at `config/config.example.json`, and the CLI will use `config/config.json` automatically when it exists.
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
  "database_url": "postgresql://seamtech:${POSTGRES_PASSWORD}@localhost:5433/seamtech_search"
}
```

PostgreSQL is the required database for normal deployments. SQLite remains available only as an explicit local/test fallback by setting `database_path` and leaving `database_url` empty.

To run PostgreSQL locally with Docker and make it reachable from Windows host tools:

```powershell
Copy-Item .env.example .env
# edit .env and replace both change-me values
docker compose up -d postgres
$env:SEAMTECH_TEST_DATABASE_URL = "postgresql://seamtech:YOUR_PASSWORD@127.0.0.1:5433/seamtech_search"
pytest -q -m postgres
```

The PostgreSQL integration test is skipped when `SEAMTECH_TEST_DATABASE_URL` is not set. The desktop launcher creates `.env`, generates local credentials, starts Docker Desktop and PostgreSQL automatically, and passes the connection URL to the backend without exposing it in the browser. The same connection URL can be placed in `config/config.json` as `database_url` for manual indexing and serving.

## Desktop Launcher

Double-click the `SEAMTECH Search` Desktop shortcut, or run:

```powershell
.\SEAMTECH Search.cmd
```

The launcher starts or reuses PostgreSQL, starts the FastAPI backend in the background, waits for its health endpoint, ensures the Next.js frontend has a production build, starts that frontend on port 3000 (or 3001 when port 3000 is occupied), and opens the browser at the selected local URL. Docker Desktop, Node.js and pnpm are required on the Windows host; credentials are generated automatically on first launch and stored only in the ignored `.env` file.

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

## Run The Backend API

```powershell
python -m seamtech_search serve --config config/config.json
```

The command serves the FastAPI backend at `http://127.0.0.1:8000`. To use the browser interface on a Windows host, run `SEAMTECH Search.cmd`; the launcher starts the Next.js frontend and opens `http://127.0.0.1:3000`.

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

`auth_token` is a single shared secret for the whole deployment: anyone holding it can search and open anything under every configured `root_path`. This is an intentional, accepted trade-off for a single trusted internal team; it does **not** provide per-client or per-folder access separation. If that separation is ever needed, this is the first thing to redesign — rotate the token periodically (`SEAMTECH_AUTH_TOKEN` env var overrides the config file) and treat it as a shared team secret, not a per-user credential.

### Search backend contract

SQLite (FTS5, `bm25` ranking, prefix-OR token matching) and PostgreSQL (`plainto_tsquery`, `ts_rank_cd`) do not guarantee identical result ordering or matches for the same query — they are two different full-text engines, not two drivers for the same one. **PostgreSQL is the contractual backend**: its behavior is what a production deployment should be validated against. SQLite is for local development and the test suite only; don't use it to sanity-check production search behavior, and don't expect query-for-query parity between the two.

An indexing run aborts without removing old records if a configured root is unavailable or traversal reports an error. Successful runs record their status and counters in the `scan_runs` table.

## Docker Deployment

`docker compose up` starts three services: `postgres`, the FastAPI backend (`web`), and the Next.js `frontend`. Copy `.env.example` to `.env` and fill in real values first:

```powershell
Copy-Item .env.example .env
# edit .env: set POSTGRES_PASSWORD and SEAMTECH_AUTH_TOKEN to real secrets
docker compose up --build
```

Then create `config/config.json` (see the example above) with your real `root_paths` before indexing.

- Frontend: `http://localhost:3000` — the UI to use day to day.
- Backend API: `http://localhost:8000` — exposed for direct API access/debugging; the frontend never needs this URL from the browser, since it proxies server-side.

`SEAMTECH_AUTH_TOKEN` is required in Docker: compose runs the backend with `SEAMTECH_HOST=0.0.0.0` and `SEAMTECH_ALLOW_NETWORK_ACCESS=true` so the `frontend` container can reach `web` over the internal Docker network (a container bound to `127.0.0.1` is only reachable from inside itself), and `AppConfig` requires a token whenever the host is non-local. The same token is shared by both containers server-to-server; it's still never sent to the browser. For a native/single-host run (no Docker), leave `config/config.json`'s `host` at `127.0.0.1` and skip this — the env override only applies to the containers it's set for.

Run indexing from the host machine when the host has access to the Windows shared folders. The launcher has already prepared PostgreSQL automatically; for a scheduled job, set `SEAMTECH_DATABASE_URL` from the generated local configuration or use a managed PostgreSQL connection:

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
- `seamtech_search/`: Python package containing the crawler, indexer, API and CLI.
- `frontend/`: the only web UI, used by both Docker and the native desktop launcher. Its server-side `app/api/*` routes proxy requests to the FastAPI backend without exposing backend credentials to the browser.
- `tests/`: regression tests

## Recommended Deployment

**Supported topology: a single Windows host or VM with direct access to the shared folders, running both indexing and the web server.** This matches the `.cmd` launcher and PowerShell scripts already in this repo and keeps indexing, preview, and "Open File" operating against the same filesystem view — there is no split between where files are indexed and where they're opened from.

```powershell
python -m seamtech_search index --config config/config.json   # scheduled via Task Scheduler
python -m seamtech_search serve --config config/config.json   # run as a service (e.g. via NSSM)
```

Use PostgreSQL (see Docker Deployment) as the index store once the corpus outgrows the SQLite fallback; SQLite remains fine for a single small deployment or local testing.

The Docker/Postgres path in this repo is kept as an option for a Linux-hosted deployment, but it comes with a real limitation: the container has no access to the Windows shares, so indexing still has to run from a Windows host (see `scripts/run_indexing.ps1`) against the same Postgres instance, and `/open` (which calls `os.startfile`) only works when the *serving* process itself is a Windows host — it will return `501 Not Implemented` from inside the Linux container. Only choose this topology if you specifically need Postgres running on separate infrastructure from the Windows indexing host; otherwise the single-Windows-host setup above is simpler and has fewer moving parts.
