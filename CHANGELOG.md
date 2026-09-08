# Changelog

## 0.3.0 — Import workflow completion

Closes every gap from the import-pipeline audit; the search/crawl core is
unchanged apart from the shared anchor constant and the PDF extractor bump.

### Backend

- **Shared anchors** (`seamtech_search/anchors.py`): one canonical
  `TECHNICAL_ANCHORS` constant used by both the crawler and the import
  pipeline — classification can no longer drift. Added `reference` /
  `référence`, `longueur`, `largeur`, `matériau`, `mesures dessin`.
- **pdfplumber extraction** (`extractors.py`, extractor version bumped 3 → 4):
  layout-aware text plus table cells, with `pypdf` kept as an automatic
  fallback. Old PDFs are re-parsed on the next scan via version gating.
- **Unit normalization**: dimensions keep their raw values and gain `*_mm`
  normalized values plus `unit_normalized`; labeled `Longueur:/Largeur:`
  sheets are recognized alongside `L x W` patterns.
- **Two-phase import**: `POST /imports/scan` (read-only candidates with
  matched anchors) + `POST /imports/confirm` (process the chosen PDF).
  `POST /imports` keeps one-shot behaviour and now returns candidates too.
- **Word reports**: every import generates `technical-report.pdf` **and**
  `technical-report.docx` (python-docx).
- **OneDrive**: uploads all 3 files with exponential-backoff retries
  (`onedrive_max_retries`, env `SEAMTECH_UPLOAD_MAX_RETRIES`) plus
  `POST /imports/{id}/retry-upload` for later re-attempts.
- **Manual correction**: `PATCH /imports/{id}` re-validates with Pydantic,
  regenerates both reports and re-uploads.
- **JSONB storage**: `imports.payload` is `JSONB` on PostgreSQL with an
  automatic `TEXT → JSONB` migration; SQLite stays JSON text. Same JSON
  shape is returned on both backends.
- **Browser upload**: `POST /imports/upload` stages drag-and-drop bytes in an
  isolated server directory (path-escape hardened) and returns candidates.

### Frontend

- Import panel rewritten: server-path scan, candidate picker with anchor
  evidence, drag-and-drop zone, file/folder browse, upload-and-scan,
  result table with normalized dimensions, manual correction form and
  OneDrive retry button.
- New proxied API routes: `/api/imports/scan`, `/api/imports/confirm`,
  `/api/imports/upload`, `/api/imports/[id]` (GET + PATCH),
  `/api/imports/[id]/retry`.

### Tests

- `tests/test_import_workflow.py`: 21 tests over real reportlab-generated
  PDFs — anchors, units, scan/confirm, docx readability, correction API,
  3-file upload with mocked retry/backoff, and upload staging.
- Suite: **54 passed, 1 skipped** (skip = Postgres integration without a
  live DB), `tsc --noEmit` clean, Next.js production build green.
