# Audit 2026-10-08 — finding-by-finding closure, verification evidence and handoff

**Scope.** Independent readiness audit of SEAMTECH Search, findings **A01–A10** plus three
additional investigations requested during the remediation (retention vs. active work, worker
claim/ack ownership, cross-workflow consistency).

**Baseline under audit.** `570f61dcfa79f21c4b06d89790b5bb21b5670e43` (release under audit).
**Work branch.** `arena/fb7d9326-seamtech-search`.
**Commits.** `bfe89b7` (A01–A05, A06–A07, A08–A10, migration 022) and `ac7f5e9` (retention
protections, claim/ack ordering, cross-workflow validation gate, frontend fix).
**Post-fix behaviour is what is asserted** in every test below — "the faulty behaviour still
reproduces" is explicitly *not* accepted as a passing safety test.

---

## 0. What was NOT available (stated plainly)

* The audit artifacts the audit shipped (`deliverables/final-audit/FINAL_AUDIT_2026-10-08.md`,
  `reproduce_findings.py`, `reproductions.json`, `reproductions.log`, `frontend-route-probe.log`,
  `merged-main-ci-jobs.tsv`) are **not present in this checkout** and could not be retrieved from
  `origin`, from any branch, from `gh pr list`, or by a filesystem search. They were **not read**:
  every finding below was re-derived from the source and re-produced independently.
* **A09 and A10 were source-review findings, not executed reproductions.** They are treated here
  as source-review findings throughout: their fixes are verified by type-check, production build,
  an executable browser spec (CI), an HTTP-level check of the relay (local), and a static pin.
  No claim is made that the pre-fix browser behaviour was executed and observed.

---

## 1. Closure table

| # | Finding (pre-fix behaviour) | Fix | Code | Verification (post-fix) |
|---|---|---|---|---|
| **A01** | `POST /fiches/{code}/corriger` wrote only the review trace; typed tables (`fiche_cotes`, `fiche_renfort`, `fiche_option`, …) kept the old value, so a 6.6 → 7.7 correction changed what the reviewer saw but not what the workshop *searched* | Explicit, allow-listed field→column registry with type/unit validation and rowcount checks; correction writes the canonical row **and** refreshes the weighted search text in the same transaction; unsupported targets are *said* (`non_supportee`) instead of reporting a silent success; divergent history is diagnosed and reconciled only on explicit request | `fiches/corrections_canoniques.py`, `fiches/routes.py` | `tests/test_audit_a01_a02_integrite.py` (9 tests): canonical row changes, search text gains `7.70` *and* `7,70` and loses the old value, `GET /recherche` finds it; invalid value/unit → 422 with **no** trace, data, journal or revision change; identity field correction is trace-only and reported as such. Strength: restoring the pre-fix `routes.py` fails 7 of these tests |
| **A02** | Re-extraction replaced an `a_valider` fiche **without advancing `revision`**: a stale screen could still validate content nobody had displayed | Write path takes `FOR UPDATE`, bumps `revision` in the same statement (`… RETURNING`), and publishes `ResultatEcriture(id_fiche, action, revision, revision_avant, remplacee)`; decisions are compare-and-swap on the revision (stale ⇒ 409, no journal row) | `fiches/persistance.py`, `fiches/depot.py`, `fiches/routes.py` | Same module: re-extraction advances the revision and a validation on the previous revision is refused with no write. Strength: restoring pre-fix `persistance.py`+`depot.py` fails the revision test |
| **A03** | Staged originals were deleted with `upload_status=not_configured` / `all_verified=false`, even when `delete_local_after_upload=False` — the only local copy of an original disappeared because a *report* uploaded | Purge is gated on **proven** integrity: `evaluer_preservation` = every file and every declared report is `uploaded` + `verified` + keyed, `files` non-empty, no keyless entry ⇒ `upload_incomplete`, and the staging tree is kept. External writable archive dirs are never purged without the explicit `SEAMTECH_DELETE_LOCAL_AFTER_UPLOAD` lever | `worker.py` (`Preservation`, `evaluer_preservation`, `decider_purge`), `import_pipeline.py` | `tests/test_audit_a06_a07_reprise.py` (11 tests, A07 half): a locally lost piece with proven reports ⇒ `all_verified False`, `upload_status "partial"`, purge **refused**, piece listed in `manquants_localement`; purge happens only when integrity is proven |
| **A04** | A failed legacy upload moved an **external** writable archive directory into quarantine while persisted retry paths still pointed at the old location | Quarantine is an explicit decision (`DecisionQuarantaine`): staging-only directories are moved; external directories report `deplace=False` and keep `source_path`. When a move does happen, every persisted reference is rewritten (`source_path`, `files[i].path`, `technical_pdf`, `report_*`, `excel_file`, plus `jobs.update_job_source_path`) | `worker.py` (`mettre_en_quarantaine`, `relocaliser_references`, `est_sous_staging`), `jobs.py` | Same module: relocation rewrites every persisted path and the DB re-read matches; external dir is not moved and stays retryable |
| **A05** | Two uploaded files named `same.txt` both returned HTTP 200; the second silently overwrote the first | Case-folded key normalisation + collision analysis **before** any write: a collision is refused (409) with the conflicting names, never merged silently | `api.py` (`_cle_insensible`, `analyser_collisions`, `/imports/upload`) | `tests/test_audit_a05_collisions.py` (9 tests): three identical names ⇒ 3 reported collisions per family, first-write-wins is replaced by refusal, ordering is key-sorted (upload order is not guaranteed) |
| **A06** | A `pending` DB job whose Redis entry was lost stayed `pending` forever after reconciliation | Reconciliation is paginated over `pending` **and** `running`, rebuilds the selection from `import_jobs.selected_pdf/selected_excel`, re-enqueues, and — when the selection is genuinely gone — fails the job **with an explicit reason** instead of substituting anything. Redis being down is reported (`redis_indisponible`), never silently treated as "no orphan" | `worker.py` (`reconcilier_file`, `_BattementDeCoeur`), `jobs.py` (`jobs_non_preserves`, `STATUTS_ACTIFS`) | `tests/test_audit_a06_a07_reprise.py` (A06 half): orphan re-enqueued through a file double; job among 250 recent ones is still found although `jobs_actifs(limite=200)` hides it; lost selection ⇒ `failed_from_db` with the reason; preserved selection re-enqueued with its `selected_pdf`. Strength: pre-fix `worker.py` fails 5 of them |
| **A07** | `/retry-upload` reported `uploaded` + `all_verified=true` after uploading only generated reports, while originals were still failed | `construire_manifeste` covers **every** `files[i]` **and** every declared report, reconducts key/bucket/verified per entry and demotes keyless `uploaded` → `pending`; `agreger_preservation` returns `uploaded/True` only when *every* entry is uploaded, verified and keyed; `("not_configured", False)` without destination | `import_pipeline.py`, `worker.py` | Same module: partial success ⇒ `all_verified False`, `upload_status "partial"`, purge refused, failures listed per entry; totals match files + declared reports; DB re-read matches. Strength: pre-fix `import_pipeline.py` fails 6 of them |
| **A08** | `components/nouveau-app.tsx` polled `/api/lots/{id}` after a deposit, but that frontend route did not exist → Next's own 404, which the screen swallowed: "Dossier traité" then nothing | New relay `app/api/lots/[id]/route.ts` (numeric id validated → 400, no backend → 503, unreachable backend → 502, backend answer relayed verbatim, no caching); the screen now **says** the tracking is unavailable (`erreur-suivi`) and its polling is generation-guarded (an abandoned lot cannot rewrite the screen and its timer stops at a terminal state) | `frontend/app/api/lots/[id]/route.ts`, `frontend/components/nouveau-app.tsx` | ① `tsc --noEmit` clean, `pnpm build` succeeds and lists `ƒ /api/lots/[id]`; ② **executed locally over real HTTP** (built frontend + real backend on PostgreSQL): `GET /api/lots` → 200 array, `GET /api/lots/3` → 200 with `id_lot 3`, `GET /api/lots/abc` → 400, `POST /api/imports/dossier` → 201 then `GET /api/lots/4` → 200 matching the new lot; ③ with a non-PostgreSQL backend the relay propagates 503 (verified locally) instead of a local 404; ④ browser spec `frontend/e2e/nouveau-suivi.spec.ts` (2 tests) — **not executed locally, no browser available** (see §4) |
| **A09** | `/etat` had a generation guard but `/pieces` callbacks did not: a late response could pair fiche B's fields with fiche A's PDF | `/pieces` responses are guarded by their own generation token (success **and** failure) and the displayed document is labelled with the fiche that produced it, so a document is only rendered when it belongs to the active fiche | `frontend/components/validation-app.tsx` | ① `tsc --noEmit` clean + production build; ② **executed locally over real HTTP**: the two e2e fiches now have *distinct* documents (`/api/fiches/7792-SO/pieces` → id 1, `/api/fiches/0901-MM/pieces` → id 2, `/api/fiches/0902-MM/pieces` → id 4) and `/api/pieces/{id}/apercu|telecharger` return `application/pdf` for each — the race is *observable*; ③ browser spec `frontend/e2e/validation-pieces.spec.ts` forces the race (A's `/pieces` delayed 3 s, switch to B, then A's answer lands) — **not executed locally, no browser available** |
| **A10** | `scripts/recette_locale.ps1` restore (`DELETE` + `pg_restore --clean`) stopped only `web`: the worker kept writing into the same tables, so the deleted row could be recreated mid-restore | Both wrappers define the writer set as `web` **and** `worker`, stop it **before** the `DELETE` and the `pg_restore`, **verify** the stop and refuse to restore when isolation cannot be proven, and guarantee restart (`trap` / `finally`) | `scripts/recette_locale.ps1`, `scripts/recette_locale.sh` | `tests/test_recette_locale.py::test_restauration_isole_tous_les_ecrivains_avant_detruire` (new static pin, both wrappers: writers named, stop ordered before destroy, stop verified, restore cancelled on doubt, restart guaranteed). Strength: with the pre-fix wrappers the pin fails. Full run is a CI-only Docker job — **not executed on a Windows target here** |

---

## 2. Additional investigations

| Investigation | Answer | Code | Verification |
|---|---|---|---|
| **Retention vs. active work** — does age-based staging cleanup destroy the only input of pending/running/retryable work? | It did. `chemins_proteges(index)` now returns the source path, `files[i].path`, `technical_pdf`, `report_*`, `excel_file` of every active (`pending`/`running`/`retry`) and every *preserved* job, plus their parent directories; `prune_reports`/`prune_staged_uploads` refuse to delete a protected path (they warn) and `run_retention_cleanup` is **fail-closed**: if the protection list cannot be established, it deletes nothing and reports `protection_indisponible` | `retention.py`, `jobs.py` | `tests/test_audit_retention_travail.py` (8 tests): a pending job's staging survives; a `not_configured` sole copy survives; an unreferenced old staging dir + report are pruned (counts 1/1); an unreadable index prunes **nothing** and sets `protection_indisponible`. Strength: pre-fix `retention.py` raises `ImportError` (feature absent) |
| **Worker claim ownership / lease / ack ordering** — multi-worker faults | `ack_task(queue, payload, worker_id=)` refuses to acknowledge when the claim no longer belongs to that worker and disables the blind "same `job_id`" fallback for ownership-aware callers; `worker_loop` checks ownership **before** acking, so a demoted worker can no longer delete a queue entry another worker is executing | `redis_store.py`, `worker.py` | `tests/test_audit_claim_ack.py` (4 tests, **real Redis**): non-owner ack returns False and the processing list is unchanged; owner ack removes exactly one entry; a stolen claim leaves the task in processing, writes no terminal state and the claim still belongs to worker B; nominal path acks, releases the claim and completes the job. Strength: pre-fix `redis_store.py`+`worker.py` fails 2 of them |
| **Cross-workflow consistency** (import → persistence → review → correction → validation → search → documents → backup/restore) | A correction that never reached the métier data could still be **published** by validation: the workshop would search the old value while the review screen showed the new one (A01's blind spot, between two workflows). Validation now reads status, revision **and** divergences in one snapshot, refuses to publish a fiche whose corrected value did not reach a supported target (409 `coherence_canonique`), ignores such a fiche in a batch with its reason (naming the fields), and offers two explicit exits: reconcile (`POST /fiches/{code}/reconcilier-corrections`) or accept and journal (`accepter_divergences=true`). Non-propagatable traces (identity fields) never block | `fiches/corrections_canoniques.py` (`divergences_sous_curseur`, `divergences_bloquantes_sous_curseur`), `fiches/routes.py` (`valider_fiche`, `valider_lot`) | `tests/test_audit_coherence_parcours.py` (5 tests, real PostgreSQL + real HTTP, real sample dossiers): refusal writes nothing (status, revision, journal, data untouched); lot validates the healthy fiche and ignores the divergent one with the field names; explicit acceptance is journalised with both values and the **published data remains the canonical one**; reconciliation unblocks validation and makes the corrected value searchable; a non-propagatable correction does not block. Strength: with the pre-fix `routes.py`+`corrections_canoniques.py` all 5 fail |

Two defects were found *by* these tests and fixed in the same commit:

* `valuer_json_sur` (added): `Decimal`/`date`/`bytes` from PostgreSQL made the diagnostic and
  reconciliation HTTP responses raise a serialization error — a 500 instead of the explanation the
  operator needs. Both reports are now JSON-safe.
* The batch-ignore reason named the *count* of divergences but not the fields; it now names them
  (first three + `…`).

---

## 3. Data compatibility & operations notes

* **Migration `022_selections_durables`** is additive: `import_jobs.selected_pdf/selected_excel`.
  Every disposable database created by the test suites runs migrations 001→022 from scratch, so a
  fresh install and an upgrade converge (verified: `schema_migrations` top row `022_selections_durables`
  on a freshly seeded database, `vector 0.7.4` plus `documents.embedding vector` present).
* **Revision semantics (`SEAMTECH_REQUIRE_REVISION`).** `valider` / `rejeter` / `rouvrir` require a
  revision (absent ⇒ 428, non-positive integer ⇒ 422, stale ⇒ 409 with **no** journal row).
  `SEAMTECH_REQUIRE_REVISION=false` is the *only* bypass and it logs a warning. Ops should keep the
  default (`true`) and make the UI send the revision it displayed.
* **Divergence acceptance is never implicit.** A validation that publishes a fiche whose corrected
  value did not reach the métier data must pass `accepter_divergences=true`; the journal comment
  then carries both values. Nothing repairs historical data automatically —
  `POST /fiches/{code}/reconcilier-corrections` needs `{"confirmer": true}` and is journalised.
* **Local copies.** `SEAMTECH_DELETE_LOCAL_AFTER_UPLOAD` (default **false**) is the only lever that
  allows purging a writable external archive directory. Staging copies are purged once integrity is
  proven. Quarantine never *moves* an external directory: it reports `deplace=False` and keeps
  `source_path`, so retry paths stay valid.
* **Retention jobs** (`SEAMTECH_REPORTS_RETENTION_DAYS=90`, `SEAMTECH_STAGED_RETENTION_DAYS=7`,
  `SEAMTECH_AUDIT_RETENTION_DAYS=365`) now need a readable index: with an unreachable index the
  cleanup deletes nothing and reports `protection_indisponible`. Expect "nothing pruned" instead of
  "everything pruned" during an incident — by design.
* **Two import paths are kept distinct** and were not merged: (a) legacy browser upload/import
  (staging + legacy import records + worker + object storage) and (b) the Métier dossier deposit
  (server-side directory + `fiche_*` extraction + attachment catalogue + métier search/validation).
  MinIO/S3 configuration does **not** make every document in every screen remotely stored; the
  per-path guarantees are covered by `tests/test_file_durable_operations.py`,
  `tests/test_partage_web_worker.py` and the `s3`-marked suite.
* **Frontend**: no visual/interaction change. The only UI-visible differences are the ones the
  findings require — the tracking block on *Nouveau dossier* now shows a tracking error when the
  relay cannot answer (`erreur-suivi`), and the *Validation* screen shows the revision/tracking
  state as before but can no longer display another fiche's document.
* **Known limitation (documented, not fixed)**: `fiche_galon` exposes only `couleur`, so corrections
  of galon width/matière stay trace-only and are reported `non_supportee`.
* `docs/REPORT.md` was corrected where it described the old, unsafe behaviour (blind purge after
  upload; `RPUSH`/`BLPOP`).

---

## 4. Verification matrix — what was run where

| Check | Status |
|---|---|
| `ruff check` (E,F,I,W, line-length 120) + `compileall` | **Local, clean** |
| SQLite suite `-m "not postgres and not s3 and not perf and not redis_queue"` | **Local: 922 passed, 3 skipped** |
| PostgreSQL + Redis suite `-m "postgres or redis_queue"` | **Local: 333 passed, 3 skipped** |
| Audit/targeted modules (A01/A02, A05, A06/A07, claim/ack, retention, cross-workflow, recette) | **Local: 67 passed** |
| CI coverage gate (`pytest -m "not s3 and not perf" --cov=seamtech_search` + `scripts/coverage_gate.py`) | **Local, reproduced and passing**: 1249 passed, 5 skipped, overall 86.8 % (floor 85 %), every per-module gate met — `jobs.py` 100 % (gate 94 %) after the PostgreSQL supervision tests |
| Migrations + `pgvector` on a fresh database | **Local: 001→022, `vector 0.7.4`, `documents.embedding vector`** |
| Real PostgreSQL + real Redis integration | **Local** (suites above; disposable databases, never the user's data) |
| Backup/restore round trip (`tests/test_sauvegarde_restauration.py`, incl. destroyed-database round trip) | **Local (PostgreSQL suite)** |
| Real MinIO upload/verify/failure/retry | **NOT executed locally** (no Docker in this sandbox), **passing in CI** (`integration` job, PR #36). Covered by the `s3`-marked suite against a labelled in-memory S3 double (`tests/s3_en_memoire.py`) here, and by CI with a real MinIO service. Left open as a target-environment check |
| Frontend `tsc --noEmit` | **Local, clean** (includes the e2e specs) |
| Frontend production build (`pnpm build`) | **Local, success** — route table includes `ƒ /api/lots/[id]` |
| A08 relay over real HTTP (built frontend + real backend) | **Local, executed** (200/400/503 + deposit → lot) |
| Browser e2e for A08/A09 | **NOT executed locally**: `playwright install chromium` fails (`cdn.playwright.dev` unreachable from this sandbox). Specs are written, type-checked and enumerated by `playwright test --list` (60 tests, 13 files) — and they **pass in CI** (`e2e` job on PR #36, 2 m 55 s) |
| A10 restore isolation | **NOT executed on a Windows host** (none available). Static pin in `tests/test_recette_locale.py`; the Docker path **passes in CI** (`recette-locale` job on PR #36) |
| Offline behaviour | `scripts/verifier_hors_ligne.py` and `frontend/e2e/hors-ligne.spec.ts` (browser part not run locally) |
| Dependency audits | `pip-audit -r requirements.txt -r requirements-dev.txt`: **no known vulnerabilities**; `pnpm audit --prod`: **no known vulnerabilities** |
| Test doubles labelled | Yes — the in-memory S3 double (`tests/s3_en_memoire.py`) is labelled as a double in its own docstring; the Redis and PostgreSQL boundaries are exercised against **real** servers locally |

---

## 5. Readiness statement

The ten audited findings and the three additional risks are **fixed, tested against the corrected
behaviour, and regression-pinned**; the pre-fix code demonstrably fails the new tests (each module's
strength was proven by restoring the previous revision and re-running). Nothing was weakened: no
assertion was relaxed, no test disabled, no marker removed, no skip silently widened.

In CI (PR #36, commit `990531b`): the browser e2e (A08/A09), the Docker restore path (`recette-locale`),
the real-MinIO integration path (`integration`), the backup/restore job (`sauvegarde`), the offline
job (`hors-ligne-reel`), `docker`, `ocr`, `recette-corpus-reel`, `scale-bench`, `securite-dependances`
and `frontend` **all pass**. Three `backend` matrix jobs went red on the **coverage gate** — the new
`jobs.py` supervision code was exercised only on SQLite, leaving the PostgreSQL branches uncovered and
`jobs.py` at 91.8 % against its 94 % gate. That was fixed here by adding the PostgreSQL supervision
tests (`tests/test_file_durable_postgres.py`), the SQLite `result`-encoding test
(`tests/test_audit_a06_a07_reprise.py`) and by removing one genuinely dead re-parsing block in
`jobs_non_preserves`; the gate now passes locally (jobs.py **100 %**, overall 86.8 %).

One verification item remains **open by infrastructure, not by choice**: restore isolation on a real
**Windows** host — no Windows machine is available here; the property is pinned statically and the
Docker equivalent passes in CI. Everything else that could not run locally (browser e2e, real MinIO)
is green in CI. Readiness therefore: **pilot-ready for the three-workshop-user flow**, with the honest
caveat that the Windows restore step is verified by its pin plus the Docker path, not on the
commanditaire's machine.

---

## 6. Reproduction

```bash
# Backend, tests (PostgreSQL on 5433, Redis on 6390 in this sandbox)
SEAMTECH_TEST_DATABASE_URL=postgresql://seamtech@127.0.0.1:5433/postgres \
SEAMTECH_TEST_REDIS_URL=redis://127.0.0.1:6390/0 \
  .venv/bin/python -m pytest -q -m "postgres or redis_queue"

.venv/bin/python -m pytest -q -m "not postgres and not s3 and not perf and not redis_queue"

# Audit modules only (64 tests)
SEAMTECH_TEST_DATABASE_URL=postgresql://seamtech@127.0.0.1:5433/postgres \
SEAMTECH_TEST_REDIS_URL=redis://127.0.0.1:6390/0 \
  .venv/bin/python -m pytest -q tests/test_audit_a01_a02_integrite.py tests/test_audit_a05_collisions.py \
    tests/test_audit_a06_a07_reprise.py tests/test_audit_claim_ack.py tests/test_audit_coherence_parcours.py \
    tests/test_audit_retention_travail.py tests/test_recette_locale.py

# Frontend
cd frontend && pnpm install --frozen-lockfile && pnpm exec tsc --noEmit && pnpm build

# Browser e2e (needs Chromium; live mode needs a disposable PostgreSQL admin URL)
SEAMTECH_E2E_DATABASE_URL=postgresql://seamtech@127.0.0.1:5433/postgres pnpm exec playwright test
```
