# SEAMTECH Search Production Readiness Report

## Summary

SEAMTECH Search is a deployable internal search application. PostgreSQL is the normal runtime database, while SQLite remains available only as an explicit local/test fallback. The web interface helps users inspect results directly with highlighted snippets, previews, and Windows open actions.

## Implemented Work

- Added PostgreSQL support through `database_url`.
- Added automatic local PostgreSQL provisioning for the Windows launcher with ignored generated credentials.
- Added an opt-in PostgreSQL integration test covering schema initialization, indexing, search, statistics and health.
- Added single-owner scan locking and a JSON indexing benchmark command for large-corpus pilot measurements.
- Added structured extraction results, persisted status/detail fields, clean metadata-only fallback for unsupported files, and UI status indicators.
- Added optional bounded LibreOffice extraction for legacy Office files and Tesseract/OCRmyPDF fallback for images and scanned PDFs.
- Added an explicit external parser registry for CAD/vendor extensions with shell-free, timeout-bounded subprocess execution.
- Hardened search snippets by rendering highlight markup as safe React text and aligned live API fields with the frontend contract.
- Protected root, health and metrics endpoints when authentication is configured, enabled strict frontend type checking, and required PostgreSQL for scheduled indexing.
- Added scan snapshots that restore the previous document index after a failed indexing run.
- Added authenticated operational endpoints, strict frontend type checking, and scheduled PostgreSQL enforcement.
- Kept SQLite support for local development and tests.
- Added PostgreSQL full-text search using `tsvector`, GIN indexing, ranking, and highlighted snippets.
- Added highlighted snippets to SQLite search results.
- Fixed relative path resolution when the configuration file is inside the `config/` directory.
- Added batched indexing to reduce database write overhead.
- Added indexing progress and throughput logs.
- Extended `/health` with backend, database size/integrity, disk capacity, and index counts.
- Added `/metrics` for search request/error counts and search timings.
- Added `/preview` for folder listings and extracted TXT/PDF/DOCX text.
- Added `/open` for Windows Open File/Open Folder actions.
- Improved result display with folder hierarchy, snippets, preview buttons, and open buttons.
- Added Docker deployment files for the web app and PostgreSQL.
- Added PowerShell scripts for indexing, launch, PostgreSQL backup/restore, and SQLite fallback backup/restore.
- Added a Desktop launcher command and created the user Desktop shortcut.
- Updated README, structure documentation, dependencies, and regression tests.

## Current Architecture

- `seamtech_search/config.py`: loads runtime settings and resolves relative paths safely from the project root when config is under `config/`.
- `seamtech_search/crawler.py`: walks configured folders and yields document metadata.
- `seamtech_search/extractors.py`: extracts text and returns structured status/detail while keeping unsupported files metadata-searchable.
- `seamtech_search/indexer.py`: owns database initialization, indexing, search, stats, health details, and backend selection.
- `seamtech_search/api.py`: exposes the FastAPI web app, search, preview, open, health, and metrics endpoints.
- `frontend/`: contains the Next.js browser interface and server-side API proxy routes.
- `scripts/`: contains operational scripts for launch, indexing, backup, and restore.

## Database Strategy

Normal deployments use PostgreSQL:

```text
postgresql://seamtech:${POSTGRES_PASSWORD}@localhost:5433/seamtech_search
```

The Docker deployment overrides this to use the Compose service hostname:

```text
postgresql://seamtech:${POSTGRES_PASSWORD}@postgres:5432/seamtech_search
```

When `database_url` is not configured, the app uses the SQLite `database_path` only as an explicit local/test fallback. The Windows launcher provisions PostgreSQL and sets `SEAMTECH_DATABASE_URL` automatically.

## Operations

- Health endpoint: `GET /health`
- Metrics endpoint: `GET /metrics`
- Search endpoint: `GET /search?q=REFERENCE&limit=50`
- Preview endpoint: `GET /preview?path=...`
- Open endpoint: `POST /open?path=...`

Recommended scheduled indexing command:

```powershell
.\scripts\run_indexing.ps1 -Config config/config.json
```

Recommended PostgreSQL backup command:

```powershell
.\scripts\backup_postgres.ps1 -DatabaseUrl "postgresql://seamtech:$env:POSTGRES_PASSWORD@localhost:5432/seamtech_search"
```

## Verification

The local verification suite passes:

```text
27 passed, 1 skipped (PostgreSQL integration requires a configured database URL)
```

Additional smoke checks completed:

- Sample data indexing completed successfully.
- CLI search returned expected sample results.
- FastAPI `/health` returned `200`.
- FastAPI `/search` returned highlighted snippets.
- FastAPI `/preview` returned folder preview data.
- Python compile check passed for `seamtech_search`.

## Known Deployment Notes

- PostgreSQL must be reachable before production indexing or serving with `database_url`; the Windows launcher handles this automatically through Docker Desktop.
- The Windows Open File/Open Folder feature uses `os.startfile`, so it is intended for a Windows host where the server has access to the searched folders.
- If the app is run inside Docker, file opening happens inside the container context and is not the recommended mode for desktop file launching.
- OCR, LibreOffice, and external CAD/vendor parsers are optional and require their tools to be installed and explicitly enabled.
- A live PostgreSQL integration run and representative 50 GB benchmark still require the target deployment environment.


## Implementation update — version 0.2.0

The current working tree adds bounded extraction for structured text, OOXML and ZIP manifests; explicit file-size and network-policy configuration; token protection for search, preview and open; safe scan-abort behavior when configured roots cannot be fully traversed; durable scan status records; connection timeouts; safer SQLite FTS tokenization; browser token/header support; and structured extraction status for unsupported, skipped, failed and timed-out files. The automated suite now contains 24 passing tests plus one opt-in PostgreSQL integration test, with Python compilation and the Next.js production build passing.

This remains production-oriented rather than universally production-certified. Enterprise SSO, per-folder ACL inheritance, process-isolated extraction, a live PostgreSQL environment, real factory-share testing and a representative 50 GB benchmark still require the factory environment and security decisions.
