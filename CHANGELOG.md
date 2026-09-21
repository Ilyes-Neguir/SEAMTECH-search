# Changelog

## Unreleased — Lot A : schéma métier & migrations 006-009 (`lot-a/schema-metier`)

- **Image PostgreSQL → `pgvector/pgvector:pg16`** (docker-compose + service PostgreSQL du job
  d'intégration CI). `unaccent` et la configuration `seamtech_unaccent` (migration 005) restent
  disponibles dans cette image — vérifié par les tests live (`to_tsvector('seamtech_unaccent', …)`
  toujours opérationnel après migrations).
- **`seamtech_search/schema_metier.py` (nouveau)** — SQL des quatre migrations, transcrit du §6.2 du
  plan v3.0, rendu idempotent (`IF NOT EXISTS` partout) :
  - `006_fiche_technique` : extension `vector` ; référentiels (client, bateau, type_voile, materiau,
    utilisateur, gabarit) ; commande, fiche (+index), fiche_cotes, fiche_materiau, fiche_galon,
    fiche_jonction, fiche_finition, fiche_option, fiche_renfort, fiche_mesure_libre, fiche_lien,
    fiche_champ_extrait (+index partiel corrige), fiche_validation, fiche_anomalie, chunk (embedding
    `vector(384)`, tsv généré sur `seamtech_unaccent`) ; extension de `documents` (id_fiche, role,
    embedding, traite_le) ; vue `v_fiche_recherche`. 21 tables.
  - `007_recherche_index` : extension `pg_trgm` ; `fiche.champs_texte` + `fiche.search_vector` ; index
    GIN plein-texte et trigrammes (code, titre, champs_texte) ; fonction
    `rafraichir_texte_recherche_fiche(BIGINT)` (pondération A=code+titre, B=référentiels et champs
    structurés, C=notes — appelée à la validation d'une fiche, jamais en boucle) ; synonyme ;
    recherche_log.
  - `008_ml_corpus` : ml_modele, ml_exemple (origine synthetique|reel), ml_run. Aucun modèle binaire
    en base : seul le chemin du fichier est stocké.
  - `009_qualite_et_gabarits` : gabarit_test (valeurs attendues en JSONB) ; vue `v_qualite` (passage
    direct, corrections, validations) ; reprise idempotente des index fiche(statut) et
    fiche_champ_extrait(corrige).
- **Décision actée dans le code (§17.1)** : couche métier PostgreSQL uniquement — sur SQLite,
  006-009 ne font rien (warning + migration enregistrée). Commentaire en tête de module et sur chaque
  migration pour éviter toute « restauration de parité SQLite ». Test de décision
  `test_sqlite_ne_recoit_pas_la_couche_metier`.
- **Tout le SQL validé par pglast** : le harnais `tests/test_postgres_sql_grammar.py` parcourt
  `run_migrations()` et parse chaque émission avec libpg_query — les quatre nouveaux scripts sont
  couverts automatiquement.
- **/health enrichi** : `schema_migrations` (versions appliquées), `schema_metier_a_jour`, et présence
  EFFECTIVE des extensions (`vector`, `pg_trgm`, `unaccent` via `pg_extension`). Le diagnostic échoué
  dégrade la réponse (warning journalisé), jamais le service.
- **Tests** — `tests/test_migrations_metier.py` (8, marqueur `postgres`, base jetable par test) :
  base vide → 27 tables métier créées ; idempotence (rejeu sans effet) ; capacités réelles
  (`SELECT '[1,2,3]'::vector`, similarité pg_trgm, `seamtech_unaccent`) ; insertion fiche +
  `v_fiche_recherche` + `v_qualite` + fonction 007 ; `/health` ; démarrage réel de l'application
  (TestClient, `/ready` + `/health`) sur base métier ; mesure de taille. Le tout-SQLite reste vert.
- **Mesures (PostgreSQL 17.11 + pgvector 0.8, serveur local — la CI rejoue sur l'image pg16)** :
  migrations 006-009 sur base vide : **0,09 s** ; schéma métier créé (tables vides, index inclus) :
  **~728 ko** ; suite live `-m postgres` : 13 passés.
- Aucune dépendance Python ajoutée (pgvector et pg_trgm sont des extensions PostgreSQL).

### Constat 1 de revue — privilèges PostgreSQL (correctif appliqué sur cette branche)

- La configuration de recherche n'est plus référencée en dur dans le DDL : marqueur `__TS_CONFIG__`
  injecté au moment de la migration avec la configuration EFFECTIVE (`seamtech_unaccent`, repli
  `simple` — même dégradation gracieuse que la migration 005). Vise `chunk.tsv` (006) et la fonction
  de rafraîchissement (007). Un rôle sans privilège ne bloque donc PLUS le démarrage sur ce point.
- `vector` (extension non « trusted ») restant obligatoire, son échec de création reste fatal mais
  porte désormais un message actionnable (image pgvector/pgvector:pg16 ou préinstallation par
  l'administrateur) — testé contre un rôle réellement non superutilisateur.
- `pg_trgm` (« trusted » mais exigeant CREATE sur la base) : les index trigrammes deviennent
  dégradables — cœur de 007 appliqué, index omis avec avertissement et conséquence journalisés.
- `/health` : clé `extensions.applicables` ajoutée côté PostgreSQL (symétrie avec la branche SQLite).
- Nouveaux tests : échec actionnable (live), migrations passant avec rôle limité + vector
  préinstallé (live), injection `simple` validée pglast (unitaire). Doc : `docs/DEPLOYMENT.md`
  (section « Privilèges PostgreSQL requis par la couche métier »).

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
