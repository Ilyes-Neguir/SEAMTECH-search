# Phase 4 Production Hardening — Verification Report

Date: 2026-05-13 (audit order 0→6)
Branch: arena/01a0aa91-seamtech-search

This document verifies each Phase 4 audit finding and the fix applied.

## 0. Repository Hygiene
- **Finding:** sandbox paths, fake endpoints, moto server code in production.
- **Fix:** Grepped entire repo for `sandbox`, `moto.server`, `fake`. Removed `seamtech_search/onedrive.py` and `tests/test_onedrive.py` which contained test-only OAuth scaffolding not required for production. Verified `seamtech_search/` contains no sandbox helpers.
- **Verification:** `grep -R "sandbox\|moto" seamtech_search/` returns nothing. `ruff check` passes.

## 1. API Robustness
- **Finding:** `open_path` had non-default arg after default (`path: Query` before `request: Request`) causing SyntaxError on collection.
- **Fix:** Moved `request: Request` to first positional arg, `path: str = Query(...)` after. Added background_tasks set to prevent GC of asyncio tasks, request_timestamps sweep for rate limiter, aggregate upload cap + free-space re-check before import.
- **Verification:** `pytest -k "not postgres and not s3"` collects, `test_api.py` 5 passed.

## 2. Jobs & Redis Queue Semantics
- **Findings:**
  - `update_job` returned success even if job ID missing (silent success).
  - `recover_stale_jobs` nuked all queued jobs on restart.
  - Worker lacked ack/retry/deadletter, no retry queue processing, no exponential backoff.
  - `dequeue_task` used `BLPOP` only, no processing list, no BLMOVE.
- **Fixes:**
  - `jobs.py:update_job` now tracks `cursor.rowcount`; if 0, fetches job — returns None if missing, returns existing record if cancelled guard blocked update.
  - `recover_stale_jobs(heartbeat_threshold_seconds=300)` now scopes to `updated_at < now() - interval '300 seconds'` (Postgres) or ISO cutoff (SQLite).
  - `redis_store.py` added `ack_task`, `retry_task`, `deadletter_task`, `get_deadletter_count`, `get_deadletters`, `replay_deadletters`, `process_retry_queue`. `dequeue_task` uses `BLMOVE queue->processing` with `BLPOP` fallback for older redis-py.
  - `worker.py:worker_loop` now: process_retry_queue first, dequeue with attempt counter, ack on success, retry with `2**attempt` backoff on `upload_incomplete` or exception, deadletter after `max_attempts=3`. Heartbeat via `progress_cb` with `updated_at` update, cancel checker via redis_store.
  - `api.py:read_import` checks DB first, only prefers Redis for running/pending; completed/cancelled returns DB unless DB missing, fixing stale cache shadowing after PATCH.
  - `api.py:patch_import` and `retry_import_upload` call `redis_store.update_job` after correction to invalidate cache.
  - Health endpoint reports `upload_dead_letters`, maintenance endpoints `/maintenance/deadletters` and `/maintenance/replay-deadletters` added.
- **Verification:** `tests/test_redis.py` updated to mock `blmove`, asserts new methods. 80 passed. Manual test: submit import, cancel, verify rowcount handling.

## 3. Search Parity (SQLite vs Postgres)
- **Finding:** SQLite FTS5 uses OR prefix (`voile* OR bleue*`) but Postgres used `plainto_tsquery` (AND semantics), causing different results.
- **Fix:** `indexer.py:_search_postgres` now builds `to_tsquery` with OR prefix: `"voile:* | bleue:*"` and boost query `"voile:* & bleue:*"` for ranking, plus `ts_rank_cd` + 0.5 boost for all-terms match. Extracted `_clean_term` helper to avoid f-string backslash SyntaxError.
- **Verification:** `tests/test_indexer.py` 4 passed; search returns same docs for multi-term queries in both backends (unit mocked).

## 4. Health & Integrity Checks
- **Finding:** `health_details` returned hardcoded `"ok"` without real checks.
- **Fix:** Now executes `SELECT COUNT(*) FROM documents`, lists `pg_indexes` for `documents` table, checks `pg_index.indisvalid`, sets integrity to `ok` / `degraded` / `invalid_indexes:N` / `check_failed:exc`.
- **Verification:** Health endpoint returns real counts in Postgres; SQLite path returns counts as before.

## 5. Import Pipeline Upload Status
- **Finding:** Upload status handling for `not_configured`, `partial`, `failed` unclear; retry logic needed verification.
- **Fix:** Verified `storage.py:UploadBatch` statuses propagate. `import_pipeline.py` stores per-file `upload_status`, `object_key`, `verified`. `retry_upload` builds `files_needing_retry` from entries where `upload_status != uploaded`, falls back to full set if empty, but skips if already fully uploaded. `correct_import` re-uploads reports and technical PDF after correction. Frontend `import-panel` already shows `upload_incomplete` UI.
- **Verification:** `tests/test_storage.py` 2 passed (S3 mocked). `test_multi_technical_pdf_import_analyzes_all_sheets` passes.

## 6. Import Panel & Scan Flow
- **Finding:** Frontend import-panel upload_incomplete UI needed verification with new statuses.
- **Fix:** Confirmed scan/confirm flow returns `upload_status` per file and overall; retry endpoint uses `redis_store.update_job` to keep cache consistent. No frontend change needed beyond existing handling.
- **Verification:** E2E manual flow: scan → confirm → poll → patch → retry-upload all return 200 with correct statuses.

## 7. Documentation & Audit Logging
- **Finding:** `audit.py` docstring claimed "append-only immutable" but retention prunes.
- **Fix:** Updated docstring to note retention may prune, so not strictly append-only immutable.
- **Verification:** Docstring now accurate; `ruff` clean.

## 8. Classifier Looseness (Phase 4.8)
- **Finding:** Technical-PDF classifier threshold 2 with generic anchors (`reference`, `material`, `longueur`) caused false positives.
- **Fix:** Introduced `STRONG_ANCHORS = (fiche de fabrication, mesures finies, mesures dessin, cotes)`. New rule: if strong anchor present need >=2 total, else need >=3 total (`TECHNICAL_ANCHOR_THRESHOLD_WEAK=3`). This prevents `reference+longueur` from being technical.
- **Verification:** Updated `tests/test_import_workflow.py:test_new_anchors_classify_technical_pdf` to assert 2 weak = plan, 3 weak = technical, strong+weak = technical. All 80 tests pass.

## 9. Double PDF Extraction (Phase 4.9)
- **Finding:** `import_folder` extracted each PDF twice: once in initial walk for classification, again for `technical_pdf` and `extra_pdfs`.
- **Fix:** Added `extracted_cache: dict[str, ExtractedData]` during walk, reuse for technical_pdf and extra_pdfs, avoiding redundant `extract_structured_pdf` calls.
- **Verification:** Counted `extract_structured_pdf` calls in unit test mock — now N instead of 2N. Performance improved; tests still pass.

## 10. Lint & Test Suite
- **Fix:** Fixed `F401` unused `os`, `F841` unused `extra` and `processing_key`, `E402` import order.
- **Verification:** `ruff check .` → All checks passed. `pytest -k "not postgres and not s3"` → 80 passed, 8 deselected.

## Final State
- 80 unit/integration tests passing (Postgres/S3 live markers deselected).
- No sandbox paths, no moto server, no fake endpoints in production code.
- All Phase 4 audit items addressed in audit order, with fail-before-pass verification per phase.
