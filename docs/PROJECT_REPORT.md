# SEAMTECH Search
## Technical and Professional Project Report

**Project type:** Internal engineering file-search platform  
**Version:** 0.2.0  
**Platform:** Windows desktop deployment with optional Docker/PostgreSQL services  
**Repository:** `Ilyes-Neguir/SEAMTECH-search`  
**Report date:** 2026-09-02

## 1. Executive Summary

SEAMTECH Search is an internal search application for engineering, design, manufacturing, and client project folders. It crawls configured Windows directories, indexes file and folder metadata, extracts bounded searchable text from supported formats, and presents results through a Next.js web interface.

The system is designed for teams that need to locate technical references quickly across large folder structures. It supports filename, folder, path, extension, and extracted-content search. The application also provides previews and Windows Open File/Open Folder actions when the serving process has access to the indexed filesystem.

The production database direction is PostgreSQL. SQLite remains available only as an explicit local and test fallback. The application includes scan safety controls, structured extraction statuses, optional OCR and legacy Office extraction, and a controlled external parser registry for CAD or vendor-specific formats.

## 2. Objectives

The project addresses the following operational problem:

- Engineering files are distributed across many folders and client directories.
- File names and folder structures are not always consistent.
- Proprietary and uncommon formats make universal content search impossible.
- Manual browsing is slow and difficult to reproduce.
- A failed or incomplete scan must not silently delete valid historical index records.

The main objectives are:

1. Provide fast metadata and content search.
2. Preserve file-system safety and configured-root boundaries.
3. Use PostgreSQL as the normal production index store.
4. Keep unsupported formats searchable by metadata.
5. Make extraction failures visible instead of hiding them.
6. Support incremental indexing so unchanged files are not repeatedly parsed.
7. Provide a practical Windows launcher for non-technical users.
8. Make performance measurable before indexing a full archive.

## 3. System Architecture

The application has four principal layers.

### 3.1 Windows launcher

`SEAMTECH Search.cmd` is the desktop entry point. It invokes the PowerShell launcher from the repository directory. The launcher:

1. Ensures local PostgreSQL is available through Docker Desktop.
2. Starts the FastAPI backend if it is not already healthy.
3. Builds the Next.js frontend on first use.
4. Starts the frontend through `pnpm start`.
5. Selects port 3000, or port 3001 when 3000 is occupied.
6. Waits for health endpoints.
7. Opens the browser automatically.

### 3.2 FastAPI backend

The Python service owns configuration loading, crawling, extraction orchestration, indexing, search, preview, health, metrics, and safe Windows open operations.

Important backend routes:

- `GET /` service information
- `GET /health` database and index health
- `GET /metrics` search and health metrics
- `GET /search?q=...` paginated search
- `GET /preview?path=...` safe file/folder preview
- `POST /open?path=...` Windows host open operation

### 3.3 Indexing and extraction

The crawler walks configured roots without following directory symlinks. Each file receives metadata, a stable normalized path key, an extraction result, and an extraction status.

The indexer performs batched upserts and maintains scan records. A scan lock prevents concurrent indexing. A failed scan restores the previous document index and retains the failed scan record.

### 3.4 Next.js frontend

The frontend provides the user-facing search interface. Its server-side API routes proxy requests to FastAPI, so backend URLs and authentication tokens are not exposed to the browser.

The interface supports:

- Search input and pagination
- Match classification
- Highlighted snippets rendered safely as React content
- File and folder icons
- File metadata and dates
- Extraction-status indicators
- Preview panel
- Copy path action
- Windows open action
- Index health summary

## 4. Technologies Used

### Backend

- Python 3.11 or newer
- FastAPI 0.116.1
- Uvicorn 0.35.0
- Pydantic 2.11.7
- psycopg2-binary 2.9.10
- pypdf 5.8.0
- python-docx 1.2.0
- pytest 8.4.1
- httpx 0.28.1

### Database

- PostgreSQL 16 for normal deployments
- PostgreSQL `tsvector` full-text search
- PostgreSQL GIN search index
- SQLite FTS5 for explicit local/test fallback

### Frontend

- Next.js 16.3.3
- React 19
- TypeScript 5.7.3
- Tailwind CSS 4
- SWR
- Lucide React
- pnpm

### Operations

- Docker Desktop
- Docker Compose
- PowerShell
- Windows batch launcher
- Optional LibreOffice
- Optional Tesseract
- Optional OCRmyPDF
- Optional trusted CAD/vendor parser executables

## 5. Configuration

The main configuration file is `config/config.json`. A safe template is provided in `config/config.example.json`.

Typical production settings include:

```json
{
  "root_paths": [
    "\\\\SERVER\\SEAMTECH\\DesignFiles",
    "D:\\SEAMTECH\\Clients"
  ],
  "database_url": "postgresql://seamtech:REPLACE_LOCALLY@127.0.0.1:5433/seamtech_search",
  "host": "127.0.0.1",
  "port": 8000
}
```

Relative paths are resolved from the project root when configuration is stored in the `config` directory.

### Configuration groups

- `root_paths`: folders allowed for crawling, preview, and open operations
- `database_url`: PostgreSQL connection URL
- `database_path`: SQLite fallback path only
- `excluded_names`: directory/file names excluded from crawling
- `excluded_extensions`: extensions excluded from crawling
- `max_extract_chars`: maximum indexed extraction output
- `max_file_size_bytes`: maximum file size eligible for extraction
- `extraction_timeout_seconds`: parent extraction timeout
- `enable_legacy_office`: enables LibreOffice conversion
- `libreoffice_command`: LibreOffice executable name/path
- `enable_ocr`: enables image and scanned-PDF OCR paths
- `tesseract_command`: Tesseract executable name/path
- `ocrmypdf_command`: OCRmyPDF executable name/path
- `external_extraction_timeout_seconds`: timeout for external tools
- `external_extractors`: extension-to-command registry for trusted parsers
- `auth_token`: shared deployment authentication token
- `allow_network_access`: explicit opt-in for non-local binding

## 6. Docker and PostgreSQL Credentials

Actual passwords and authentication tokens are intentionally not included in this report or committed to Git.

### Credential names

Docker Compose uses:

- `POSTGRES_PASSWORD`: password for the PostgreSQL `seamtech` database user
- `SEAMTECH_AUTH_TOKEN`: shared backend/frontend authentication token

### Automatic credential behavior

The Windows launcher uses `scripts/ensure_postgres.ps1` to:

1. Create `.env` if it does not exist.
2. Generate a random PostgreSQL password.
3. Generate a random authentication token.
4. Store them locally in `.env`.
5. Start Docker Desktop when necessary.
6. Start PostgreSQL through Docker Compose.
7. Export the connection URL only to the current launcher process.

`.env` is ignored by Git. Credentials are never sent to the browser and must not be pasted into source files, screenshots, reports, or commits.

### Credential rotation

To rotate local credentials:

1. Stop the project services.
2. Remove or edit the local ignored `.env` file.
3. Start the launcher again.
4. For an existing PostgreSQL volume, change the database password using PostgreSQL administration procedures or recreate the disposable development volume deliberately.

For production, use a managed secret store or protected environment variables rather than storing credentials in a repository file.

## 7. Ports and Network Topology

### Native Windows launcher

| Component | Address | Purpose |
|---|---|---|
| FastAPI backend | `127.0.0.1:8000` | API and health service |
| Next.js frontend | `127.0.0.1:3000` | Browser UI when available |
| Next.js fallback | `127.0.0.1:3001` | Used when port 3000 is occupied |
| Docker PostgreSQL | `127.0.0.1:5433` | Host-accessible PostgreSQL |

The project uses host port 5433 because another PostgreSQL service may already use port 5432.

### Docker Compose internal topology

| Component | Container address | Host mapping |
|---|---|---|
| PostgreSQL | `postgres:5432` | `127.0.0.1:5433` |
| Backend | `web:8000` | `127.0.0.1:8000` |
| Frontend | container port 3000 | `127.0.0.1:3000` |

The frontend talks to `web:8000` internally. The backend talks to PostgreSQL at `postgres:5432` internally. The host-side integration test uses `127.0.0.1:5433`.

## 8. How to Use the Application

### First-time Windows setup

Requirements:

- Windows PowerShell
- Python 3.11+
- Node.js
- pnpm
- Docker Desktop

From the project directory:

```powershell
.\SEAMTECH Search.cmd
```

The first launch may install frontend dependencies and build Next.js. It also starts PostgreSQL automatically through Docker Desktop.

### Configure real folders

Edit `config/config.json` and replace the sample root path with the real Windows folders or network shares. Verify that the account running the indexer has read permissions.

### Index files

```powershell
.\.venv\Scripts\python.exe -m seamtech_search index --config config/config.json
```

Use a deliberate full rebuild only when required:

```powershell
.\.venv\Scripts\python.exe -m seamtech_search index --config config/config.json --rebuild
```

### Search from the terminal

```powershell
.\.venv\Scripts\python.exe -m seamtech_search search CLIENT-123 --config config/config.json
```

### View statistics

```powershell
.\.venv\Scripts\python.exe -m seamtech_search stats --config config/config.json
```

### Run the benchmark

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_indexing.py --config config/config.json
```

The benchmark reports scanned items, changed records, removed records, elapsed seconds, and items per second. A real 50+ GB benchmark should be run against a representative copy or pilot share.

### Scheduled indexing

```powershell
.\scripts\run_indexing.ps1 -Config config/config.json
```

Scheduled indexing requires `SEAMTECH_DATABASE_URL` and therefore does not silently fall back to SQLite.

## 9. Supported File Formats

### Content extraction included

- Plain text and source/configuration files
- CSV, TSV, JSON, XML, YAML, SQL, logs, and common source files
- PDF files with embedded text
- DOCX
- XLSX, PPTX, ODT, ODS, and ODP through bounded XML extraction
- ZIP member manifests

### Optional extraction

- `.doc`, `.xls`, `.ppt` through LibreOffice conversion
- Images through Tesseract OCR
- Scanned PDFs through OCRmyPDF fallback
- CAD/vendor formats through explicitly configured external parsers

### Metadata-only fallback

Unsupported files remain searchable by:

- File name
- Folder name
- Full path
- Extension

This includes many CAD, PDM/PLM, vendor-specific, binary, email, archive, and legacy formats when no parser is configured.

### Extraction statuses

Each indexed record can report:

- `extracted`: content was extracted
- `unavailable`: no parser, disabled feature, or unsupported format
- `skipped`: file exceeded configured extraction limits
- `error`: parser or file error
- `timeout`: extraction exceeded the allowed time
- `not_applicable`: directory record

## 10. Security Controls

- Root containment validation for preview and open operations
- Localhost binding by default
- Explicit opt-in for non-local binding
- Authentication token required for network mode
- Authentication on root, health, metrics, search, preview, and open routes when configured
- Backend credentials kept server-side
- Safe React rendering of snippets without raw HTML injection
- Parameterized SQL queries
- Shell-free external parser invocation
- Per-file and external-tool timeouts
- Single-owner indexing lock
- Failed-scan restoration
- Excluded temporary/lock files and configured extensions

The authentication token is a shared deployment secret, not per-user authorization. It does not replace enterprise SSO, per-folder ACLs, or audit logging.

## 11. Data Safety and Large-Corpus Behavior

The crawler is lazy and does not load the entire directory tree into memory at once. Unchanged files are skipped based on size, modification time, and extractor version. Index updates are batched.

A scan lock prevents concurrent indexers. A scan snapshot restores the prior document index after a failed scan. The index records scan status and counters for operational review.

The current benchmark is a smoke test on a small sample corpus. Scaling to 50+ GB depends primarily on file count, network latency, parser mix, and available memory rather than total bytes alone. A production pilot should record:

- Total files and folders
- Average file size
- Percentage of PDFs and Office files
- Number of archives
- Number of unsupported formats
- Network or local storage location
- First-scan duration
- Incremental-scan duration
- Peak memory and CPU usage
- Search latency under normal load

## 12. Testing and Verification

The project includes unit, API, indexing, scan-safety, extraction, and PostgreSQL integration coverage.

Verified checks include:

- Configuration validation
- Network policy validation
- Relative path resolution
- SQLite schema and FTS indexing
- PostgreSQL schema initialization
- PostgreSQL upsert and full-text search
- PostgreSQL statistics and health
- Incremental reindex skipping
- Extractor version invalidation
- Unsupported-format metadata fallback
- Structured extraction status persistence
- OCR/legacy Office disabled behavior
- External parser execution
- Hung parser timeout and process termination
- Concurrent scan rejection
- Failed scan restoration
- API authentication
- API pagination
- Frontend response contracts
- Strict TypeScript validation
- Next.js production build
- Docker Compose configuration

Latest local verification:

```text
29 tests passed, 1 skipped
PostgreSQL integration test passed against PostgreSQL 16
Next.js production build passed
Python compilation passed
Docker Compose configuration validated
```

The skipped test is the opt-in PostgreSQL test when no test URL is supplied during ordinary local runs. A live integration run was also completed separately against a fresh PostgreSQL 16 container.

## 13. Known Limitations

- A representative 50+ GB production benchmark has not been completed.
- Proprietary CAD and vendor formats require trusted vendor tools or licensed SDKs.
- OCR quality depends on image quality, language configuration, and installed tools.
- The shared token does not provide per-user or per-folder authorization.
- The Windows open action requires the serving process to run on a Windows host with filesystem access.
- SQLite and PostgreSQL full-text search behavior is not identical; PostgreSQL is the production contract.
- PostgreSQL connection pooling, advanced migrations, and enterprise operational monitoring can be added for larger deployments.
- The current timeout compatibility test retains a test-only monkeypatched thread; production extraction uses terminable worker processes.

## 14. Deployment Recommendation

For a Windows team that needs to open files directly from search results, use a Windows host or VM with access to the shared folders. Run PostgreSQL separately or through Docker Desktop, use PostgreSQL as the index store, and schedule indexing with Task Scheduler.

For a containerized deployment, place PostgreSQL and the backend/frontend on protected infrastructure. Mount the required data roots deliberately and do not assume a Linux container can perform Windows `os.startfile` operations.

Before indexing the full archive:

1. Configure one representative pilot root.
2. Run the benchmark.
3. Verify unsupported-format counts and extraction statuses.
4. Confirm PostgreSQL backups and restore procedures.
5. Test the frontend with real users.
6. Expand to the full archive only after performance and permissions are understood.

## 15. Conclusion

SEAMTECH Search has evolved from a local search prototype into a structured internal search platform with PostgreSQL support, safe indexing behavior, a web interface, optional format integrations, and operational tooling. Its architecture deliberately separates metadata search from content extraction, allowing uncommon files to remain discoverable without falsely claiming universal parsing support.

The system is suitable for controlled deployment and pilot benchmarking. Production certification for a 50+ GB archive still depends on the target file count, network environment, PostgreSQL operations, parser mix, security model, and measured benchmark results. The next professional deployment step is a controlled pilot using the real PostgreSQL instance and a representative subset of the factory archive.
