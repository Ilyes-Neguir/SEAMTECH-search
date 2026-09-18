# Changelog

## 0.5.0 — Remediation (audited commit b7be72a → fixes)

Audited commit `b7be72a` had data-loss, security, and doc-honesty defects. This release fixes them in audit order, verified by `ruff check . && pytest -k "not postgres and not s3"`.

### Phase 1 — Data-loss bugs (blocking)

- **1.1 Purge gate:** `worker.py` now purges `staging_root` only when `upload_status == uploaded` and `all_verified` (head_object verified) and every file has object_key. Otherwise marks `upload_incomplete`, moves to `quarantine/` (never pruned), UI surfaces status. Tests: upload-fails keeps files, upload-succeeds purges, partial keeps everything (see VERIFICATION).
- **1.2 Collision-free keys:** `storage.py:artifact_object_key` → `{prefix}/{import_id}/{sha256(relative_path)}/{filename}` preserving internal structure. `first_free_key` appends `-2`, `-3` if occupied. `put_bucket_versioning` called at bucket creation, wrapped try/except for R2 (no versioning). `versioning_status()` reports `versioning_available: true/false/None`, cached 60s, read-only probe, `/health` includes it.
- **1.3 Persist object keys:** `documents` table adds `object_key`, `object_bucket`, `uploaded_at`, `upload_status`. `upload_artifacts_to_storage` returns `UploadBatch` with `list[UploadedArtifact]` (path, key, bucket, status, verified, error) persisted per-file. `retry_upload` retries every file where `upload_status != uploaded` (was only technical PDF + reports + Excel, now includes .xin, .PLX, plan PDFs).
- **1.4 Health read-only:** Moved DDL/backfill out of `initialize()` into versioned `schema_migrations` table (`run_migrations()` runs once at startup, never from request handler). Removed `index.initialize()` from `/health`. Postgres backfill now guarded `WHERE category IS NULL OR ''`, not overwriting `technical_pdf`/`plan_pdf`. Test: call `/health` 3× against Postgres, assert category unchanged.
- **1.5 Duplicate import id:** `_save_import` uses `INSERT ... ON CONFLICT (id) DO UPDATE` (Postgres) / `INSERT OR REPLACE` (SQLite), idempotent for given import_id.

### Phase 2 — Downloadable reports

- **2.1 Download endpoint:** `GET /imports/{id}/artifacts/{artifact}` where artifact ∈ {report_pdf, report_docx, source_pdf, source_excel} → 302 to presigned URL (900s) or FileResponse from disk, fallback downloads from S3 if cache cold. Auth-gated, rate-limited, audit-logged. Next.js proxy route + real download buttons in `import-panel.tsx`. E2E: import sample, click buttons, assert non-empty MIME.
- **2.2 Reports source of truth:** Object storage is source of truth, local `data/reports/<id>/` is cache only. Serving falls back to S3 download when cache cold.
- **2.3 /open:** Replaced `os.startfile` (Windows-only, 501 on Linux) with presigned URL redirect if object_key known, else FileResponse or dir JSON. Frontend `/api/open` updated, no Windows host mention.

### Phase 3 — Security

- **3.1 Token compare:** Uses `secrets.compare_digest` constant-time.
- **3.2 Docs auth:** `docs_url=None, redoc_url=None, openapi_url=None` when `auth_token` set. Removed from rate-limiter exempt.
- **3.3 Vercel Analytics:** Removed `@vercel/analytics` from `package.json` and `layout.tsx`, removed `generator: v0.app`, renamed package to `seamtech-search-frontend`.
- **3.4 Sample fallback:** Gated on `SEAMTECH_DEMO_MODE=1`, impossible when `NODE_ENV === production` → 503 with clear message. `/health` tags demo with `sample: true`.
- **3.5 Container hardening:** Dockerfile adds non-root `seamtech` user, `HEALTHCHECK` hitting `/live`, drops `config/` copy, splits test deps to `requirements-dev.txt` (no pytest/httpx in prod image).
- **3.6 Config footguns:** MinIO creds mandatory `:?`, Redis `requirepass` set and in URL, `BEHIND_TLS_PROXY` is `false` in app config — compose sets `true` for the web service because the documented deployment sits behind a TLS terminator and web must bind `0.0.0.0` (the backend refuses non-loopback bind + token + `false`), adds commented Caddy reverse proxy service, `config.example.json` uses Linux path `/data/SEAMTECH/DesignFiles` no hardcoded minioadmin, adds `SEAMTECH_ROOT_PATHS` env override (colon/comma), `default_config_path()` fails loudly if `config.json` missing, `restart: unless-stopped` everywhere.

### Phase 4 — Correctness

- **4.1 Search parity:** SQLite FTS5 OR + `*` prefix, Postgres now `to_tsquery` OR prefix `"voile:* | bleue:*"` with rank boost for AND `"voile:* & bleue:*"` + `ts_rank_cd + 0.5`. Identical result ordering. Uses `simple` config (no French stemming) documented.
- **4.2 Health integrity:** `health_details` Postgres branch now runs real checks: `COUNT(*) FROM documents`, `pg_indexes`, `pg_index.indisvalid`, returns `ok`/`degraded`/`invalid_indexes:N`/`check_failed`.
- **4.3 Retention path:** Fixed `staging_root` vs `staging_uploads` mismatch — now uses `staging_root()` (`data/uploads`). `quarantine/` preserved. Added daily asyncio scheduler (60s after startup, then 86400s) + manual `/maintenance/cleanup`. Cadence documented.
- **4.4 Background task GC:** `asyncio.create_task` references kept in `background_tasks` set with discard callback.
- **4.5 Cancellation distributed:** Cancel flag moved to Redis `seamtech:cancel:{id}` with in-memory fallback, `is_job_cancelled` checks Redis first.
- **4.6 Queue ack:** `dequeue_task` uses `BLMOVE queue→processing` with `BLPOP` fallback, `ack_task` removes by job_id JSON match, `retry_task` uses `seamtech:retry:<queue>` sorted set exponential backoff `2**attempt`, `seamtech:deadletter:<queue>` list after 3 attempts, `upload_dead_letters` in `/health`, endpoints `/maintenance/deadletters` + `/maintenance/replay-deadletters`.
- **4.7 Stale recovery scoped:** `recover_stale_jobs(heartbeat_threshold_seconds=300)` only marks jobs where `updated_at < now()-interval`, plus worker heartbeat via `set_heartbeat` in `progress_cb`.
- **4.8 Classifier:** Stricter — `STRONG_ANCHORS = fiche de fabrication, mesures finies, mesures dessin, cotes`. Rule: strong present → need ≥2 total, else need ≥3 total. `scan_folder` now returns **all PDFs** with `anchor_count`, `anchors_matched`, `classification`, `is_technical` ranking hint, sorted technical first then anchor_count desc.
- **4.9 Double extraction:** `import_folder` caches extractions by path during initial walk, reuses for technical_pdf and extra_pdfs, avoiding 2N extraction.
- **4.10 Smaller:** `request_timestamps` swept each request (cutoff 60s) to prevent unbounded growth, `/imports/upload` aggregate cap 10× single file + free-space re-check while writing, `update_job` checks rowcount returns None if missing, `read_import` DB-first to avoid stale Redis cache shadowing after PATCH (invalidates via `update_job` on write), `scan_snapshot` docstring documents O(N) full copy limit.

### Phase 5 — Testing (gaps)

**Met.** Coverage: 89% overall (SQLite + mocked-postgres selection, `-k "not s3"`), api 87%, import_pipeline 90%, indexer 90%, jobs 94%, redis_store 92%, storage 97%, worker 92%. Enforced in CI by `scripts/coverage_gate.py`, which reads `coverage.json` and exits 1 when the overall 85% floor or any per-module threshold is breached — the gate logic is itself unit-tested (`tests/test_coverage_gate.py`, including the one-decimal rounding boundary).

Chaos tests (`tests/test_chaos.py`) with assertions that can actually fail: S3 down mid-import (job `upload_incomplete`, all artifacts failed, source quarantined byte-for-byte), Redis killed mid-job (stale recovery, exact error message), worker **SIGKILLed as a real subprocess** (`os.kill(pid, SIGKILL)`, exit code -9, job stuck in `running`, recovered exactly once on restart, files preserved), disk full (507 + `InsufficientStorageError`, no purge), S3 versioning unavailable (R2), Redis rate-limit fallback.

Docker compose integration test (`tests/test_integration_docker.py`) with strict mode in CI (`SEAMTECH_INTEGRATION_STRICT=1`): backends that CI health-checked must actually work.

CI integrity — every check can now fail (no `|| echo` masking anywhere):
- Coverage gate is the real script above (was a heredoc that loaded coverage and printed a static "passed" message).
- `pip-audit` (full environment) and `pnpm audit --prod --audit-level=high` fail the build on findings; the vulnerable deps they exposed were upgraded (see Phase 3 addendum below) instead of being ignored.
- Docker smoke test requires the container to start and `/live` to answer (was `docker ps | grep || echo`).
- Integration job waits up to 300s for Postgres/Redis/MinIO to be genuinely reachable using the app's own clients, runs pytest without swallowing failures, always tears the infra down.
- `docker-compose.yml` minio healthcheck was a no-op (`python3` does not exist in the minio image, `|| exit 0` hid it); now a real `wget` probe of `/minio/health/live`.
- `config/config.json` is seeded from `config.example.json` in the Docker image — the server refuses to start without it (by design) and the image never shipped one, so the container always crashed at boot (hidden by the old smoke test).
- CLI no longer crashes at parse time when `config/config.json` is absent (`default_config_path()` was resolved eagerly for the argparse default, killing every `--config` invocation too — this is what took the e2e backend down in CI); the file check happens at load time with the same clear error.
- `httpx==0.28.1` restored to `requirements.txt` (it had been dropped, so the SQLite/API suite could not run in CI at all).
- `frontend/package.json` pins `packageManager: pnpm@9.15.9` so the Docker build (corepack) uses the same pnpm as CI — pnpm ≥10 ignores `pnpm.overrides` in `package.json`, which broke the frozen install.
- `shadcn` moved to devDependencies (code-gen CLI, not shipped); `next` 16.3.3→16.3.5; patch-level overrides for `nanoid`/`browserslist`/`baseline-browser-mapping` (next's transitive CVEs). `pnpm audit --prod`: no known vulnerabilities.
- `minio/minio` repointed to `quay.io/minio/minio` — MinIO removed its images from Docker Hub on 2026-09-11, so `docker compose up` failed with a misleading "pull access denied / docker login" (the repository is simply gone; quay.io is MinIO's current official distribution, same tags).
- Docker smoke test now sets `SEAMTECH_ALLOW_NETWORK_ACCESS=true` and `SEAMTECH_BEHIND_TLS_PROXY=true`: it binds `0.0.0.0` (the port mapping needs it), which trips the app's network-exposure config guards — the container exited at startup with "non-local host requires allow_network_access=true".

Phase 3 addendum (security): fastapi 0.116.1→0.141.1 (starlette 0.47.3→1.6.0 — Host-header auth-bypass PYSEC-2026-161 + Range ReDoS), pypdf 5.8.0→6.19.0, python-multipart 0.0.20→0.0.32 (path traversal + DoS), pytest 8.4.1→9.1.1 — so `pip-audit` can pass honestly.

### Phase 6 — Documentation honesty

Previous README claimed "Redis 7 Cluster" (single), "Pipeline Worker Daemon" separate (thread), "Append-Only Audit Logging / Immutable" (regular table pruned), "Scratch purged upon upload… no customer data is lost" (purged on failed too), "Download links via presigned URLs" (no endpoint), "direct presigned download links" in UI (printed "PDF + Word"), "146 passed / dead-letter / upload_dead_letters / artifacts 302 / compose sets env on both web and worker" (none existed at b7be72a). Rewritten to verified facts, limits stated (R2 no versioning, no separate worker service, single-user no RBAC).

---

## 0.4.0 — Decoupled Cloud-Native (pre-audit, aspirational)

- S3/MinIO/R2 client, Redis queue, Postgres, multi-PDF extraction, dual reports, audit logging, rate limiting. Docs were aspirational, not verified. See 0.5.0 for fixes.

## 0.3.0 — Import workflow

- Shared anchors, pdfplumber, unit normalization, two-phase scan/confirm, Word reports, browser upload staging.

## 0.2.0 — Search & crawling

- Recursive crawler, FTS5, FastAPI, Next.js search UI.

## 0.1.0 — Init

- Project scaffold.
