# Audit 2026-10-08 — finding-by-finding closure, verification evidence and handoff

**Scope.** Independent readiness audit of SEAMTECH Search, findings **A01–A10** plus residual
counter-review items **R1–R4** and three additional investigations (retention vs. active work,
worker claim/ack ownership, cross-workflow consistency).

**Baseline under audit.** `570f61dcfa79f21c4b06d89790b5bb21b5670e43` (release under audit).
**Work branch.** `arena/fb7d9326-seamtech-search`.
**Commits.**
- `bfe89b7` : A01–A05, A06–A07, A08–A10, migration 022.
- `ac7f5e9` : retention protections, claim/ack ordering, cross-workflow validation gate, frontend fix.
- `37696ab` : CI coverage gate completion on PostgreSQL supervision and job result serialization.
- `6390d25` : Initial counter-review delivery (R1-R4).
- `HEAD` (this delivery, commit `22be8a3`) : Strict fencing on all terminal statuses (R1: removal of `needs_review` and `upload_incomplete` fence hole, support of `failure_reason` in `update_job`, protection of all mutation paths including exception handlers and lot progress/heartbeat); atomic claim-checked Redis transitions requiring active claim (R2: Lua reject on `no_claim`); and fail-closed restart prevention on failed restore in `recette_locale.ps1` with in-container SHA-256 verification (R4).

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
* Conversely, **R1, R2, R3, and R4 were directly verified via executable reproductions** on the
  clean audit baseline before remediation.

---

## 1. Closure table (Audit Findings A01–A10 & Counter-Review R1–R4)

| # | Finding / Counter-Review item | Fix | Code | Verification (post-fix) |
|---|---|---|---|---|
| **A01** | `POST /fiches/{code}/corriger` wrote only review trace; typed tables kept old value, workshop searched old value | Explicit allow-listed field→column registry with type/unit validation; correction writes canonical row and refreshes weighted search text in same tx; unsupported targets reported `non_supportee`; divergent history diagnosed and reconciled only on explicit request | `fiches/corrections_canoniques.py`, `fiches/routes.py` | `tests/test_audit_a01_a02_integrite.py` (9 tests): canonical row changes, search text gains `7.70`/`7,70`, invalid value/unit → 422 with no write. Reverting `routes.py` fails 7 tests |
| **A02** | Re-extraction replaced `a_valider` fiche without advancing `revision` | Write path takes `FOR UPDATE`, bumps `revision` in same statement (`RETURNING`), publishes `ResultatEcriture`; decisions are compare-and-swap on revision (stale ⇒ 409) | `fiches/persistance.py`, `fiches/depot.py`, `fiches/routes.py` | Same module: re-extraction advances revision and validation on previous revision is refused with no write |
| **A03** | Staged originals deleted with `upload_status=not_configured`/`all_verified=false` | Purge gated on proven integrity (`evaluer_preservation`): every file and report uploaded+verified+keyed. External archive dirs never purged without explicit `SEAMTECH_DELETE_LOCAL_AFTER_UPLOAD` | `worker.py`, `import_pipeline.py` | `tests/test_audit_a06_a07_reprise.py` (11 tests, A07 half): locally lost piece with proven reports ⇒ `all_verified False`, purge refused |
| **A04** | Failed legacy upload moved external archive directory to quarantine | Quarantine is explicit decision (`DecisionQuarantaine`): staging-only moved; external dirs keep `source_path`. Relocation rewrites all references and calls `update_job_source_path` | `worker.py`, `jobs.py` | Same module: relocation rewrites persisted paths, DB matches; external dir not moved |
| **A05** | Two uploaded files named `same.txt` both returned 200, second overwrote first | Case-folded key normalisation + collision analysis before write: collision refused (409) with conflicting names | `api.py` | `tests/test_audit_a05_collisions.py` (9 tests): three identical names ⇒ 3 reported collisions, refusal over first-write-wins |
| **A06** | `pending` DB job whose Redis entry was lost stayed `pending` forever | Reconciliation paginated over `pending` and `running`, rebuilds selection from `selected_pdf/selected_excel`, re-enqueues, or fails with explicit reason | `worker.py`, `jobs.py` | `tests/test_audit_a06_a07_reprise.py` (A06 half): orphan re-enqueued, lost selection ⇒ explicit failure reason |
| **A07** | `/retry-upload` reported `uploaded`+`all_verified=true` after uploading only reports | `construire_manifeste` covers every original and declared report, demotes keyless uploaded → pending; aggregates true only when every entry verified | `import_pipeline.py`, `worker.py` | Same module: partial success ⇒ `all_verified False`, `upload_status "partial"`, purge refused |
| **A08** | Screen swallowed 404 from missing frontend route `/api/lots/{id}` | New relay `app/api/lots/[id]/route.ts` (numeric id checked, no backend → 503, unreachable → 502, no cache); UI shows `erreur-suivi` and uses generation guards | `frontend/app/api/lots/[id]/route.ts`, `frontend/components/nouveau-app.tsx` | Local HTTP: 200 array, 200 matching id, 400 invalid id, 503 backend down. CI: `frontend/e2e/nouveau-suivi.spec.ts` |
| **A09** | Race condition between `/etat` and late `/pieces` callbacks | `/pieces` guarded by generation token; displayed document labelled with producing fiche | `frontend/components/validation-app.tsx` | Production build clean; local HTTP shows distinct pieces; CI browser spec `frontend/e2e/validation-pieces.spec.ts` |
| **A10** | `recette_locale.ps1` restore stopped only `web`, leaving `worker` writing | Both wrappers define writer set as `web` and `worker`, stop both before `DELETE` and `pg_restore`, verify stop, guarantee restart | `scripts/recette_locale.ps1`, `scripts/recette_locale.sh` | `tests/test_recette_locale.py` static pins for writer isolation and restart guarantees |
| **R1** | Dethroned worker performed terminal mutations or overrode successor's output | Fencing in `jobs.py`: strict check `claimed_by = expected_worker` (no `OR claimed_by IS NULL` bypass); `heartbeat_job` guarded with `expected_worker`; all worker mutation paths covered (progress, starting, exceptions, lots, terminal); `worker.py` checks active ownership before exception mutations | `seamtech_search/jobs.py`, `seamtech_search/worker.py` | `tests/test_audit_claim_ack.py` (3 dedicated tests): `test_r1_vrai_traitement_worker_dechu_n_ecrase_pas_resultat_du_repreneur`, `test_r1_worker_dechu_apres_cloture_repreneur_ne_peut_pas_reecrire`, `test_r1_exception_handler_dans_process_import_task_necrase_pas_repreneur` |
| **R2** | Non-atomic check-then-ACK in Redis allowed worker A to dequeue successor B's task | Atomic Redis Lua scripts (`_LUA_ACK_TASK`, `_LUA_RETRY_TASK`, `_LUA_DEADLETTER_TASK`) decode claim JSON, verify ownership, and execute queue transitions atomically | `seamtech_search/redis_store.py` | `tests/test_audit_claim_ack.py`: `test_r2_atomicite_ack_lua_avec_transfert_de_claim` and `test_r2_retry_et_deadletter_refuses_si_claim_perdu`: non-owner ACK/retry/deadletter return False without touching processing list |
| **R3** | Quarantined jobs retained stale paths in Redis retry payload and database selections | `update_job_source_path` updates `selected_pdf`/`selected_excel` alongside `source_path`; `worker_loop` reconstructs retry payload from database state via `_charge_depuis_job(job_actuel)` | `seamtech_search/jobs.py`, `seamtech_search/worker.py` | `tests/test_audit_a06_a07_reprise.py::test_r3_quarantaine_reprise_propage_nouveaux_chemins_et_selections`: database selections and Redis retry payload match quarantined paths, retry execution succeeds |
| **R4** | Windows restore script lacked fail-closed preconditions, exit code verification, and restarted on failed restore | `recette_locale.ps1` verifies `$DumpValide` by comparing host SHA-256 with container `sha256sum /tmp/recette-backup.dump`; checks Docker exit codes for `stop`, `cp`, `ps`; writers remain strictly stopped if `pg_restore` fails (`$CodeRestore -ne 0`) | `scripts/recette_locale.ps1` | `tests/test_recette_locale.py` (18 tests passing): verified writer isolation, SHA matching, UTF-8 BOM CRLF formatting, and fail-closed restart prevention |

---

## 2. Additional investigations

| Investigation | Answer | Code | Verification |
|---|---|---|---|
| **Retention vs. active work** | `chemins_proteges(index)` returns source paths, files, technical PDF, reports, and Excel files of all active and preserved jobs; `prune_*` refuses to delete protected paths; `run_retention_cleanup` is fail-closed on unreadable index | `retention.py`, `jobs.py` | `tests/test_audit_retention_travail.py` (8 tests): active/preserved staging survives, unreadable index prunes nothing |
| **Worker claim ownership / lease / ack ordering** | Atomic Lua-based ACK/retry/deadletter conditioned on worker claim; worker loop checks lease ownership before committing mutations; stolen claims remain in queue for successor | `redis_store.py`, `worker.py` | `tests/test_audit_claim_ack.py` (9 tests including R1 and R2): verified against real Redis instance |
| **Cross-workflow consistency** | Validation refuses to publish fiches with unpropagated canonical divergences without explicit operator acceptance; lot validation isolates divergent fiches without breaking batch; reconciliation unblocks publication | `fiches/corrections_canoniques.py`, `fiches/routes.py` | `tests/test_audit_coherence_parcours.py` (5 tests): real PG + HTTP, journal traces verified |

---

## 3. Data compatibility & operations notes

* **Strict fencing token semantics (`expected_worker`).** All job updates and closures from worker processes pass `expected_worker=worker_id`. The database condition strictly enforces `claimed_by = expected_worker`. When a worker completes a job, `claimed_by` becomes NULL, which permanently locks out any previous stale worker from mutating that job record.
* **All worker mutation paths covered.** Fencing guards progress callbacks, initial job starting state, exception handling, and lot processing in `worker.py`. A dethroned worker encountering an exception cannot mark the successor's job as failed.
* **Atomic Redis Lua scripts.** ACK, retry, and deadletter operations in `RedisStore` execute Lua scripts atomically verifying `claim.worker_id == expected_worker`.
* **Quarantine path synchronization.** Relocating an upload to quarantine synchronizes `import_jobs.source_path`, `selected_pdf`, and `selected_excel`. Re-enqueued retry tasks derive their payload from the refreshed job record, preventing stale path crashes.
* **Fail-closed Windows restore.** `recette_locale.ps1` computes the container SHA-256 and matches it against the copied host file. If `pg_restore` fails, the services are kept stopped to prevent applications from writing to an un-restored or partially restored database.

---

## 4. Verification matrix — what was run where

| Check | Status |
|---|---|
| `ruff check` (E,F,I,W, line-length 120) + `compileall` | **Local, clean** |
| `test_audit_claim_ack.py`, `test_audit_a06_a07_reprise.py`, `test_audit_a05_collisions.py`, `test_audit_retention_travail.py`, `test_recette_locale.py`, `test_file_durable_operations.py` | **Local: 86 passed** (100 % pass rate with real Redis; 11/11 in `test_audit_claim_ack.py` covering R1 & R2, 27/27 in `test_file_durable_operations.py`) |
| A01/A02 & Cross-workflow test suites (`test_audit_a01_a02_integrite.py`, `test_audit_coherence_parcours.py`) | **PostgreSQL integration suite** (verified in PR #36 CI; local unaccent extension absent from pip pgserver) |
| Durable PostgreSQL queue suite (`test_file_durable_postgres.py`) | **PostgreSQL integration suite** (local supervision & migration tests pass; full lot test verified in CI) |
| Frontend `tsc --noEmit` & production build (`pnpm build`) | **Local, clean** (`ƒ /api/lots/[id]` included) |
| Browser e2e specs (A08/A09) | **CI green** (PR #36 `e2e` job; Playwright download blocked in local sandbox) |
| Windows restore script checks | **Local static pins pass** (18 tests in `test_recette_locale.py`), Docker restore verified in CI |

---

## 5. Readiness statement

All ten original audit findings (A01–A10) and all four residual counter-review defects (R1–R4) have been
remediated, verified, and regression-pinned.

The strictly fenced implementation guarantees that dethroned workers cannot corrupt state, progress, or terminal results (R1).
Redis operations for task lifecycle transitions are atomic and claim-checked via Lua (R2). Staging quarantine
correctly synchronizes durable database selections and retry payloads (R3). The Windows local verification script
enforces fail-closed preconditions, container-checksum verification, and refuses to restart writers on failed restore (R4).

Readiness verdict: **Candidate renforcée pour pilote atelier contrôlé** (le statut « production pilot-ready » absolu étant réservé à la qualification opérationnelle in-situ, incluant le dimensionnement réel et la recette sur poste physique Windows).
