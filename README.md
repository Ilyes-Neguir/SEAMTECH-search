# SEAMTECH Search

Internal file search, technical dossier ingestion, and synthesis report platform for SEAMTECH sail manufacturing.

**État réel au 22/09/2026** (cet état remplace toute mention antérieure de
« Verified state ») :

- **Ce qui est établi** : extraction réglée mesurée 6/6 sur UNE vraie fiche
  client (7792-SO) ; recherche hybride PostgreSQL 13/13 sur le jeu réel et
  50/50 sur le jeu synthétique de référence ; latence produit p95 < 100 ms
  mesurée HORS instrumentation (étape CI dédiée, publiée en ::notice) ;
  parcours machine complet de validation 582-665 ms sur la vraie fiche
  (annotation CI `mesure-phase1`, trois runs verts consécutifs) ; source
  vecteurs active ; CI 7 jobs. Détails, commandes et sorties brutes :
  `docs/verite_terrain/TRACABILITE_LIVRAISON.md` (une ligne par affirmation,
  statut ✅ établi / ⚠️ démontré une fois / ❌ non mesuré).
- **Où sont les mesures** : `docs/verite_terrain/` — `EMPREINTES.md`
  (extraction), `JEU_REQUETES_REELLES.md` (recherche, auto-limites écrites),
  `MESURE_VALIDATION_2MIN.md` (chrono machine + procédure humaine),
  `FUSION_MAIN.md` (runbook de fusion + répétition générale), CHANGELOG
  (deux colonnes repli / e5 réel pour chaque chiffre Lot F).
- **Comment lancer** : `docker compose up -d` (voir Quickstart) ; tests :
  `pytest -q -m "not postgres"` (487/3 sans PostgreSQL), avec PostgreSQL :
  `-m "postgres and not perf"` (intégration) puis `-m perf` (latence, sans
  instrumentation) ; front : `pnpm build && pnpm start`.
- **Ce qui n'est PAS prouvé** : le chrono humain de validation (0/3 fiches
  mesurées — dernier verrou de la Phase 1) ; l'échelle réelle (une seule fiche
  réelle aujourd'hui ; 20-30 attendues) ; la sauvegarde testée par une
  restauration (R2/S3) ; la calibration (`calibre: false`, volontaire).
- **Sécurité** : le dépôt contient une vraie fiche client et doit passer
  PRIVÉ ; le jeton GitHub ayant circulé doit être révoqué (règle : un
  document client ne se versionne pas — l'archive est la source de vérité,
  RG13).

---

## Architecture (honest)

```
Browser → Next.js Frontend (proxy) → FastAPI API → PostgreSQL 16 + pgvector (FTS + métier) + Redis 7 (single) + S3 (MinIO/R2/AWS)
                                      │
                                      └─ Background worker thread (Redis BLMOVE queue, not separate service)
                                      └─ Retention scheduler (daily, preserves quarantine)
                                      └─ Object Storage is source of truth, local reports are cache
```

- **Object Storage (MinIO / R2 / AWS S3):** Durable store. Keys are collision-free: `{s3_prefix}/{import_id}/{sha256(relative_path)}/{filename}`. Existing keys are never overwritten — next free `-2`, `-3` suffix is used. Bucket versioning is requested at creation (best-effort: Cloudflare R2 does not implement `PutBucketVersioning`, so `versioning_available: false` is reported in `/health` and suffix protection is used). Every upload is verified via `head_object` before local purge is allowed.
- **Database (PostgreSQL 16 via `pgvector/pgvector:pg16` — Lot A requires the `vector` extension; SQLite fallback for the legacy file index only, the fiche layer is PostgreSQL-only per plan §17.1):** Stores document index, `tsvector` GIN search, `JSONB` import payloads, `object_key`/`object_bucket`/`uploaded_at`/`upload_status` per document, `import_jobs` with `updated_at` heartbeat, `schema_migrations` versioned migrations, and `audit_log` (regular table, **not immutable** — pruned by retention after `audit_retention_days`, default 365).
- **Redis 7 (single container, not cluster):** `RPUSH`/`BLMOVE` queue → processing list, `seamtech:retry:<queue>` sorted set with exponential backoff `2**attempt`, `seamtech:deadletter:<queue>` list after 3 attempts, `seamtech:job:{id}` cache 24h TTL, `seamtech:cancel:{id}` flag for distributed cancellation, sliding-window rate limiter 600 req/min via sorted sets. `/health` reports `upload_dead_letters`.
- **API (FastAPI):** Stateless except for scratch. Scratch `data/uploads/<uuid>_<folder>` is purged **only** when `upload_status == uploaded` and every artifact verified (`all_verified`). On failure, import is marked `upload_incomplete`, moved to `data/quarantine/` (never pruned), and surfaced in UI. `/health` is read-only, cheap, does not call `initialize()`. Docs (`/docs`, `/openapi.json`) disabled when `auth_token` set, and not exempt from rate limiting. Auth uses `secrets.compare_digest` constant-time.
- **Worker:** `worker.py` `process_import_task` handles upload verification, quarantine, and purge gating. `worker_loop` processes retry queue first, acks on success, retries with backoff, deadletters after max attempts. Cancellation survives process boundaries via Redis flag + in-memory fallback. Background tasks kept in strong reference set to prevent GC.
- **Frontend (Next.js 16):** No Vercel Analytics, no `v0.app` metadata. Package name `seamtech-search-frontend`. Sample data fallback only when `SEAMTECH_DEMO_MODE=1` and never in production (`NODE_ENV === production` → 503). Download buttons link to `GET /imports/{id}/artifacts/{artifact}` which 302s to presigned URL (900s expiry) or serves file / downloads from S3 if cache cold.

**Limits / non-goals:**
- Single-user token auth, no RBAC.
- No separate `worker` service in `docker-compose.yml` — worker is thread inside `web`. Real separate worker process would need its own container.
- No `Redis Cluster`, single Redis.
- Audit log not immutable.
- Search uses `simple` tsvector (no French stemming) with OR prefix matching (`term:* | term:*`) and rank boost for all-terms (`&`). Identical semantics on SQLite (FTS5 `OR` + `*`) and Postgres.
- `scan_snapshot` does full table copy (`CREATE TABLE AS`) for rollback — O(N) cost, okay for <100k docs, at scale should use transaction savepoint.
- Retention runs daily via asyncio scheduler (60s after startup, then 86400s) plus manual `POST /maintenance/cleanup`. Cadence documented here.

---

## Quickstart

```bash
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD, REDIS_PASSWORD,
#            SEAMTECH_AUTH_TOKEN, SEAMTECH_UI_PASSWORD, SEAMTECH_SESSION_SECRET
docker compose up -d
```

> **Note registre MinIO (2026-09-24)** : plus aucun registre ne publie
> l'image MinIO (quay.io supprimé, docker.io retiré le 2026-09-11,
> dl.min.io « 410 Gone — projects archived »). Avant `docker compose up`,
> construire l'image locale depuis les sources officielles archivées :
> `bash scripts/construire_image_minio.sh` (la CI fait de même).

Services (all `restart: unless-stopped`, bound to `127.0.0.1`):

| Service | URL | Notes |
|---|---|---|
| Frontend | http://localhost:3000 | Proxies to backend; sign-in required at `/login` |
| Backend | http://localhost:8000 | `/live`, `/ready`, `/health`, `/metrics` |
| MinIO S3 | http://localhost:9000 | API |
| MinIO Console | http://localhost:9001 | UI |
| Postgres | localhost:5433 | `seamtech`/`seamtech_search` |
| Redis | localhost:6379 | Requires `REDIS_PASSWORD` |

---

## Configuration

Env overrides (all `SEAMTECH_` prefixed) or `config/config.json` (must exist, no silent fallback to example):

| Var | Purpose | Default |
|---|---|---|
| `SEAMTECH_ROOT_PATHS` | Colon or comma separated search roots | required via file or env |
| `SEAMTECH_DATABASE_URL` | Postgres URL | — |
| `SEAMTECH_AUTH_TOKEN` | Shared **server-to-server** token (32+ chars), never sent to the browser | — (mandatory in compose) |
| `SEAMTECH_UI_PASSWORD` | Password the operator types at `/login` | — (mandatory in compose; unset ⇒ login fails closed with 503) |
| `SEAMTECH_SESSION_SECRET` | HMAC key signing the httpOnly session cookie | — (mandatory in compose) |
| `SEAMTECH_SESSION_HOURS` | Session lifetime | `12` |
| `SEAMTECH_SECURE_COOKIES` | Force the `Secure` cookie flag | auto from TLS proxy / `X-Forwarded-Proto` |
| `SEAMTECH_BEHIND_TLS_PROXY` | Allow non-localhost binding with token auth when a TLS terminator is in front | config default `false`; compose: web `true`, frontend `false` (different purposes — see `docs/TLS.md`) |
| `SEAMTECH_S3_ENDPOINT_URL`, `SEAMTECH_S3_BUCKET`, `SEAMTECH_S3_ACCESS_KEY`, `SEAMTECH_S3_SECRET_KEY` | S3 | — |
| `SEAMTECH_REDIS_URL` | `redis://:password@host:6379/0` | — |
| `SEAMTECH_MIN_FREE_BYTES` | Disk floor | 1GB |
| `SEAMTECH_RATE_LIMIT_PER_MINUTE` | — | 600 |

`config.example.json` now uses Linux path `/data/SEAMTECH/DesignFiles`, no hardcoded `minioadmin`.

---

## API (verified)

| Method | Endpoint | Notes |
|---|---|---|
| `GET` | `/live`, `/ready` | Probes |
| `GET` | `/health` | Read-only, reports `versioning_available`, `upload_dead_letters`, disk free |
| `GET` | `/search?q=` | OR prefix, rank boost for AND, identical SQLite/Postgres |
| `POST` | `/imports/scan` | Returns **all PDFs** with `anchor_count`, `anchors_matched`, `classification`, `is_technical` ranking hint (4.8) |
| `POST` | `/imports/confirm` | |
| `POST` | `/imports` | 202 async via Redis or in-process fallback, `?wait=true` for sync |
| `POST` | `/imports/dossier` | Lot C — porte A : deposit a complete fabrication folder → fiche `a_valider` + attachments, one transaction, idempotent (refusal = traced result with reason, never a 500) |
| `POST` | `/imports/dossier/lot` | Lot C — door A bis: background batch of folders (in-process thread, no Redis); resumable by calling again |
| `GET` | `/lots`, `/lots/{id}` | Lot C — batch progress, per-folder status **with failure reasons**, remaining files (`/imports/{id}` stays Phase-0 single import) |
| `GET` | `/imports/{id}` | DB-first to avoid stale Redis cache shadowing after PATCH |
| `POST` | `/imports/{id}/cancel` | Redis flag + memory fallback |
| `PATCH` | `/imports/{id}` | Correction, regenerates reports, re-uploads, invalidates Redis cache |
| `POST` | `/imports/{id}/retry-upload` | Retries every file where `upload_status != uploaded` (not just technical PDF + reports) |
| `GET` | `/imports/{id}/artifacts/{artifact}` | `artifact ∈ {report_pdf, report_docx, source_pdf, source_excel}` → 302 presigned URL (≤15 min) or FileResponse, falls back to S3 download if cache cold |
| `POST` | `/open` | Now returns 302 to presigned URL if object_key known, else FileResponse or dir JSON (no `os.startfile`) |
| `GET` | `/maintenance/deadletters`, `POST` | `/maintenance/replay-deadletters`, `POST` | `/maintenance/cleanup` | Deadletter handling + retention |
| `GET` | `/audit` | Regular table, pruned |

---| `GET` | `/fiches/{code}/champs` | Lot B.2 — trace par champ : valeur brute/normalisée, méthode, confiance, page, zone PDF, version de gabarit, corrections (PostgreSQL requis, 503 sinon) |
| `GET` | `/gabarits`, `/gabarits/{code}/versions` | Lot B.2 — registre des gabarits d'extraction, toutes versions (une version publiée reste lisible) |
| `POST` | `/gabarits/{code}/versions` | Lot B.2 — publie une NOUVELLE version (max+1, jamais destructif : les précédentes passent inactives) |
| `POST` | `/gabarits/detecter` | Lot B.2 — détection sur un PDF multipart, sans aucune écriture en base ; non-détection = `reprise_complete` |

## Testing

```bash
ruff check .
# Selection is by MARKER, never by name substring (fix R-14, 2026-09-25): a test
# that needs a service declares it (`postgres`, `s3`, `sauvegarde`, `perf`).
pytest -m "not postgres and not s3 and not perf" -q   # 715 passed, 3 skipped, 207 deselected
# With coverage (same selection CI gates on; live-postgres self-skips without a DB URL):
pytest -m "not s3 and not perf" -q --cov=seamtech_search --cov-report=term --cov-report=json:coverage.json
python scripts/coverage_gate.py coverage.json   # fails (exit 1) on any threshold breach
# Live integration (needs docker compose up):
SEAMTECH_TEST_S3_URL=http://localhost:9000 pytest -m s3 -q
```

**Coverage (current, gated in CI):** 89% overall; api 87%, import_pipeline 90%, indexer 90%, jobs 94%, redis_store 92%, storage 97%, worker 92%. CI enforces ≥85% overall plus each per-module floor via `scripts/coverage_gate.py` (which reads `coverage.json` and exits 1 on any breach). See `docs/VERIFICATION.md`.

**Docker compose full-stack proof:** `docker compose up` from clean checkout with only `.env` works; import `sample_data/CLIENT-123` → rows in Postgres, objects in MinIO with collision-free keys, downloadable reports via 302, search hit, correction re-download. Chaos (`tests/test_chaos.py`, assertions that can fail): S3 down mid-import → job `upload_incomplete`, all artifacts `failed`, source moved to quarantine byte-for-byte (no loss); Redis killed mid-job → job recoverable, marked `failed` via `recover_stale_jobs`; worker SIGKILLed **as a real subprocess** (`os.kill(pid, SIGKILL)`, exit code -9) → job stuck in `running` (no cleanup ran), recovered exactly once to `failed` on restart, files preserved; disk full → 507 + `InsufficientStorageError`, no purge.

## Phase 0 tools — archive inventory & extraction bench (real, tested)

Two read-only instruments for the fiche-technique refonte (plan v3.0). Both are proven never to modify the
scanned tree: tests hash every file (size + SHA-256 + mtime) before/after a run.

**`scripts/inventaire_archive.py` — archive inventory (Phase 0).** Walks one or more roots read-only and
reports: folder/file counts, per-type and per-year volumes (file mtime), probable duplicates (size +
SHA-256, capped at `--limite-empreinte` Mo), native PDFs vs probable scans (`unavailable: no embedded
text` marker from the extractor, OCR deliberately off), and TWO fiche views: (a) the existing anchor
classifier, (b) structural detection from the configurable lexicon `config/lexique_fiches.json`
(vocabulary + detected table grid, explained score) — plus a `desaccords` section listing documents the
current classifier misses (the Phase 0 blind spot). Gabarit families use alphabetic-label fingerprints
with page-relative positions, exact grouping then Jaccard ≥ 0.85 merge. See `docs/DETECTION_FICHES.md`. Writes `inventaire.json` + two CSVs (`;`-separated, utf-8-sig) to an
output dir that must live **outside** the scanned roots — refused otherwise. No network, no OCR, no
dependency added.

```bash
python scripts/inventaire_archive.py D:/SEAMTECH/DesignFiles --sortie ./rapports/inv-01
python scripts/inventaire_archive.py ./sample_data --sortie /tmp/inv --sans-empreintes
```

**`scripts/validate_extraction.py --verite truth.json` — extraction bench, field by field (Phase 0).**
Compares `extract_structured_pdf` output against a hand-written ground-truth JSON
(`{"fiche.pdf": {"gabarit": ..., "attendu": {"reference": ..., "dimensions": {"length": 6.6, "unit": "m"}}}}`),
prints per-field verdicts (OK / ECART / MANQUANT / SUSPECT / INATTENDU / OK_ABSENCE) and per-field /
per-gabarit / global correct-read rates with timing; `--sortie-json` exports the calibration report,
`--seuil X` turns the global rate into a hard gate. Dimensions are compared in millimetres (1 mm or 0.1 %
tolerance) so unit rendering cannot fake a miss. The report refuses to be written inside the measured
sheets' folders. Without `--verite`, the harness keeps its historical per-document review behavior
(pinned by existing tests). The plan v3.0's `benchmark_gabarit.py` is realized as this mode rather than a
second script.

Phase 0 blind spot (now covered): sheets whose labels sit outside `TECHNICAL_ANCHORS` (e.g. `Guindant`,
`Bordure`, `Tissu` on genoa variants) are classified `plan_pdf` by the current classifier. The structural
detection above catches them and the inventory lists the disagreements. Measured numbers:
`docs/PHASE0_RAPPORT.md`.

---

## Docs

- `docs/VERIFICATION.md` — per-claim reproducible commands (required by Phase 0)
- `docs/PROJECT_REPORT.md`, `docs/REPORT.md` — outdated, being rewritten to match verified facts
- `docs/TLS.md` — TLS proxy guide

---

## Security notes (fixed)

- Token compare uses `secrets.compare_digest`.
- `/docs`/`/openapi.json` disabled when auth token set, not in rate-limiter exempt.
- Frontend sample fallback gated on `SEAMTECH_DEMO_MODE=1`, impossible in production → 503.
- Backend container runs as non-root `seamtech`, has `HEALTHCHECK`, does not include `pytest`/`httpx` (split to `requirements-dev.txt`), copies `config/` and seeds `config/config.json` from `config.example.json` at build time (see `Dockerfile`).
- MinIO and Redis credentials mandatory (`:?` in compose), Redis `requirepass` set, `BEHIND_TLS_PROXY` is `false` in app config and `true` for the web service in compose (the office deployment sits behind a TLS terminator and web must bind `0.0.0.0` so the frontend container can reach it — the backend refuses non-loopback bind + token + `false`), `restart: unless-stopped` everywhere.
- `default_config_path()` fails loudly if `config.json` missing.
- OneDrive code deleted (module, config, tests, `pending_reauth` UI).
- **The UI requires sign-in.** Every route under `frontend/app/api/*` returns `401` without a valid
  httpOnly session cookie, and `app/page.tsx` redirects to `/login`. Previously the frontend
  forwarded `SEAMTECH_AUTH_TOKEN` for anyone who could reach it, with no login at all. See
  [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#authentication) for the decision and the two deliberate
  exceptions (`/api/auth/*`, and `/api/health`'s count-free liveness payload).

---

## License

Internal Proprietary — SEAMTECH. All Rights Reserved.
