# SEAMTECH Search

Internal search prototype for SEAMTECH design and technical folders.

The system scans Windows folders, extracts searchable metadata/text, stores it in a SQLite FTS5 index, and exposes a simple FastAPI web interface for quickly finding references.

## Features

- Recursive folder crawling
- File and folder metadata indexing
- Search by filename, folder name, path, extension, and extracted document text
- PDF text extraction with `pypdf`
- DOCX text extraction with `python-docx`
- SQLite FTS5 full-text search
- FastAPI `/search` and `/health` endpoints
- Simple browser interface
- Incremental indexing using file size and modified time

OCR is not included in v1. Add it later after the normal indexed search works with real data.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy config.example.json config.json
```

Edit `config.json` and set `root_paths` to the folders you want to index.

## Index Files

```powershell
python -m seamtech_search index --config config.json
```

Force a complete rebuild:

```powershell
python -m seamtech_search index --config config.json --rebuild
```

## Run The Web App

```powershell
python -m seamtech_search serve --config config.json
```

Open:

```text
http://127.0.0.1:8000
```

## API

```text
GET /health
GET /search?q=REFERENCE&limit=50
```

## Tests

```powershell
pytest
```

## Recommended Deployment

Run this on one internal server or VM that can access the shared Windows folders. Keep the SQLite index on the server, not inside the searched folders. Use Task Scheduler or NSSM to run indexing regularly.

