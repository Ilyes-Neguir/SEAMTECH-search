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

## Index Files

```powershell
python -m seamtech_search index --config config.json
```

Force a complete rebuild:

```powershell
python -m seamtech_search index --config config.json --rebuild
```

Show database statistics:

```powershell
python -m seamtech_search stats --config config.json
```

Test a search from the terminal:

```powershell
python -m seamtech_search search CLIENT-123 --config config.json
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

Search results include the file/folder name, full path, parent path, extension, size, modified date, folder/file type, and match type (`exact_name`, `name`, `path`, or `content`).

## Tests

```powershell
pytest
```

## Project Structure

- `config/`: runtime and example configuration files
- `data/`: SQLite index and generated runtime data
- `logs/`: application logs
- `sample_data/`: sample content for local development
- `scripts/`: setup and maintenance helpers
- `seamtech_search/`: Python package containing the crawler, indexer, API and CLI
- `tests/`: regression tests

## Recommended Deployment

Run this on one internal server or VM that can access the shared Windows folders. Keep the SQLite index on the server, not inside the searched folders. Use Task Scheduler or NSSM to run indexing regularly.
