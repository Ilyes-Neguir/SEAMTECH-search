# SEAMTECH Search Production Readiness Report

## Summary

SEAMTECH Search has been upgraded from a local prototype into a deployable internal search application. The main production database path is now PostgreSQL, while SQLite remains available as a local development fallback. The web interface now helps users inspect results directly with highlighted snippets, previews, and Windows open actions.

## Implemented Work

- Added PostgreSQL support through `database_url`.
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
- `seamtech_search/extractors.py`: extracts text from PDF, DOCX, and plain text files.
- `seamtech_search/indexer.py`: owns database initialization, indexing, search, stats, health details, and backend selection.
- `seamtech_search/api.py`: exposes the FastAPI web app, search, preview, open, health, and metrics endpoints.
- `seamtech_search/static/`: contains the browser interface.
- `scripts/`: contains operational scripts for launch, indexing, backup, and restore.

## Database Strategy

Production should use PostgreSQL:

```text
postgresql://seamtech:${POSTGRES_PASSWORD}@localhost:5432/seamtech_search
```

The Docker deployment overrides this to use the Compose service hostname:

```text
postgresql://seamtech:${POSTGRES_PASSWORD}@postgres:5432/seamtech_search
```

When `database_url` is not configured, the app uses the SQLite `database_path`. This keeps local development simple and keeps the test suite fast.

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
5 passed
```

Additional smoke checks completed:

- Sample data indexing completed successfully.
- CLI search returned expected sample results.
- FastAPI `/health` returned `200`.
- FastAPI `/search` returned highlighted snippets.
- FastAPI `/preview` returned folder preview data.
- Python compile check passed for `seamtech_search`.

## Known Deployment Notes

- PostgreSQL must be running before production indexing or serving with `database_url`.
- The Windows Open File/Open Folder feature uses `os.startfile`, so it is intended for a Windows host where the server has access to the searched folders.
- If the app is run inside Docker, file opening happens inside the container context and is not the recommended mode for desktop file launching.
- OCR is not included. Scanned PDFs without embedded text will not produce rich previews until OCR is added.


## Implementation update — version 0.2.0

The current working tree adds bounded extraction for structured text, OOXML and ZIP manifests; explicit file-size and network-policy configuration; token protection for search, preview and open; safe scan-abort behavior when configured roots cannot be fully traversed; durable scan status records; connection timeouts; safer SQLite FTS tokenization; and browser token/header support. The automated suite now contains 14 tests, with Python compilation and JavaScript syntax checks passing.

This remains production-oriented rather than universally production-certified. Enterprise SSO, per-folder ACL inheritance, OCR, proprietary CAD parsers, PostgreSQL integration testing, real factory-share testing and a representative 50 GB benchmark still require the factory environment and security decisions.
