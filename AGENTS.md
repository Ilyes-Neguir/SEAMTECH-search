# AGENTS.md — SEAMTECH Search

Instructions for AI coding agents (Arena and others) working in this
repository. Read this file first, then `README.md` for the measured state
of the project.

## What this is

Internal file search, technical-dossier ("fiche") ingestion and synthesis
reports for SEAMTECH sail manufacturing.

```text
Browser -> Next.js frontend (proxy) -> FastAPI API -> PostgreSQL 16 + pgvector (FTS + vectors)
                                                    + Redis 7 (job queue) + S3 (MinIO/R2/AWS)
web accepts imports and executes NOTHING; a separate `worker` service runs them.
Object storage is the source of truth; local reports are cache.
```

The honest, measured state lives in `README.md` and
`docs/verite_terrain/TRACABILITE_LIVRAISON.md` (one line per claim:
established / demonstrated once / not measured). **Never claim more than
those documents establish**, and update them when a change alters a claim.

## SEAMTECH rules (these always win — see "Precedence" below)

- **Tests are selected by MARKER, never by name substring** (fix R-14):
  a test that needs a service declares it (`postgres`, `s3`, `perf`,
  `redis_queue`, … — see `pyproject.toml`). Reference selection:

  ```bash
  ruff check .
  pytest -q -m "not postgres and not s3 and not perf and not recette_corpus and not integration_docker and not redis_queue"
  pytest -m "not s3 and not perf" -q --cov=seamtech_search --cov-report=json:coverage.json
  python scripts/coverage_gate.py coverage.json   # hard gate: overall + per-module floors
  ```

- **Dependencies are pinned and mirrored**: `requirements.txt` must equal
  `[project].dependencies` and `requirements-dev.txt` must equal
  `[project.optional-dependencies].dev`
  (`tests/test_requirements_consistency.py` enforces it). Any new
  dependency must also pass `scripts/audit_dependency_policy.py`. No
  PyTorch (ONNX e5-small embeddings only — 8 GB workstations).
- **Confidentiality**: never add a real client document (PDF/ZIP/machine
  format) to Git. `tests/test_confidentialite_depots.py` pins the full
  inventory by SHA-256; a synthetic fixture is added there consciously,
  a real document never is (reference it by fingerprint instead).
- **No swallowed blind exceptions** in `seamtech_search/` (`except:` /
  `except Exception:` -> `pass` is guarded by `scripts/audit_projet.py`).
- **Domain language is French** in identifiers, docs and vocabulary
  (fiche, gabarit, recette, seuil…). Keep existing names; do not
  "translate" the codebase.
- **Storage safety invariants** (see README): object keys are
  collision-free and never overwritten; an upload is verified by size +
  SHA-256 read-back before any local purge; failed imports go to
  quarantine, which is never pruned; `/health` is read-only and cheap.
- **Secrets only via environment** (`.env.example` is the catalogue).
  Never hardcode a secret, never commit `.env`.
- Frontend (`frontend/`, pnpm, Next.js 16 / React 19 / TypeScript):
  `pnpm build`, `pnpm start`, `pnpm test:e2e` (Playwright). The frontend
  only proxies to the API — browser code never talks to services directly.
- Full stack: `docker compose up -d` (see `GUIDE_DEMARRAGE_LOCAL.md`).

## ECC skills (curated subset, in `.agents/`)

This repo vendors 30 skills + 16 reviewer checklists from
[affaan-m/ECC](https://github.com/affaan-m/ECC) v2.2.3 (MIT), pinned by
SHA-256 in `.agents/ecc-manifest.json`. Full catalogue, exclusions and
update procedure: [`.agents/README.md`](.agents/README.md).

### How to use a skill

1. Before writing code, match the task to the routing table below and
   **open the 1–3 relevant `SKILL.md` files** (not more — protect context).
2. Follow the skill *adapted to this repo*: translate its example
   commands to the SEAMTECH commands above. Commands inside a skill are
   illustrative, never run verbatim without checking they fit.
3. After the change, run the matching reviewer checklist in
   `.agents/agents/` in-role (you cannot spawn ECC sub-agents here —
   apply the checklist yourself).
4. In your final summary, name the skill(s)/checklist(s) you used.

### Routing table

| If the task touches… | Read first |
|---|---|
| Any feature / bug fix / refactor | `.agents/skills/tdd-workflow/SKILL.md` |
| Finishing work, pre-PR verification | `.agents/skills/verification-loop/SKILL.md` |
| New helper you are about to write by hand | `.agents/skills/search-first/SKILL.md` |
| General style / readability questions | `.agents/skills/coding-standards/SKILL.md` |
| Error handling, retries, error messages | `.agents/skills/error-handling/SKILL.md` |
| Branches, commits, merges, conflicts | `.agents/skills/git-workflow/SKILL.md` |
| GitHub issues / PRs / CI / releases | `.agents/skills/github-ops/SKILL.md` |
| "Is this production-ready?" | `.agents/skills/production-audit/SKILL.md` |
| Python code in `seamtech_search/` or `scripts/` | `.agents/skills/python-patterns/SKILL.md` |
| pytest tests in `tests/` | `.agents/skills/python-testing/SKILL.md` |
| FastAPI endpoints, Pydantic schemas, auth | `.agents/skills/fastapi-patterns/SKILL.md` |
| REST contract design (naming, codes, pagination) | `.agents/skills/api-design/SKILL.md` |
| SQL, schema, indexes, FTS/pgvector queries | `.agents/skills/postgres-patterns/SKILL.md` |
| Redis queue, cache, locks, rate limiting | `.agents/skills/redis-patterns/SKILL.md` |
| Database migrations | `.agents/skills/database-migrations/SKILL.md` |
| Dockerfiles, `docker-compose.yml` | `.agents/skills/docker-patterns/SKILL.md` |
| CI/CD, deployment, rollback | `.agents/skills/deployment-patterns/SKILL.md` |
| Auth, uploads, input handling, secrets | `.agents/skills/security-review/SKILL.md` |
| Fiche/PDF structured extraction (regex rules vs ML) | `.agents/skills/regex-vs-llm-structured-text/SKILL.md` |
| Extraction/search regression benches, AI-change safety | `.agents/skills/ai-regression-testing/SKILL.md` |
| React components, hooks, RSC boundaries | `.agents/skills/react-patterns/SKILL.md` |
| React component tests (if added) | `.agents/skills/react-testing/SKILL.md` |
| Frontend performance | `.agents/skills/react-performance/SKILL.md` |
| Next.js 16 dev/build, Turbopack, `proxy.ts` | `.agents/skills/nextjs-turbopack/SKILL.md` |
| UI patterns, state management | `.agents/skills/frontend-patterns/SKILL.md` |
| Accessibility of UI components | `.agents/skills/frontend-a11y/SKILL.md` |
| Playwright end-to-end tests | `.agents/skills/e2e-testing/SKILL.md` |
| Recording an architectural decision | `.agents/skills/architecture-decision-records/SKILL.md` |
| Uncertain library/framework API | `.agents/skills/documentation-lookup/SKILL.md` |
| Frontend ↔ API contract changes | `.agents/skills/contract-first/SKILL.md` |

Reviewer checklists (apply in-role after a change): `.agents/agents/`
— `planner`, `architect`, `tdd-guide`, `code-reviewer`,
`security-reviewer`, `python-reviewer`, `fastapi-reviewer`,
`database-reviewer`, `typescript-reviewer`, `react-reviewer`,
`performance-optimizer`, `e2e-runner`, `doc-updater`,
`build-error-resolver`, `refactor-cleaner`, `rag-pipeline-reviewer`.

Update or verify the vendored skills with
`python3 scripts/sync_ecc_skills.py --check` / `--update` (see
`.agents/README.md`). **Do not hand-edit files under `.agents/skills/` or
`.agents/agents/`** — they are verbatim upstream copies pinned by
SHA-256; `tests/test_ecc_skills.py` will fail. Local adaptations belong
in this file or in `.agents/README.md`, never inside a vendored skill.

## Precedence

On any conflict, in this order: (1) the user's current instruction,
(2) this file + SEAMTECH docs/tests/config, (3) the ECC skill. Examples:
SEAMTECH's pinned-dependency policy overrides `search-first`'s "adopt a
package"; SEAMTECH's marker-based selection and per-module coverage
floors override a skill's generic test commands; SEAMTECH's French
domain naming overrides generic naming advice. ECC hooks/MCP are **not
installed** — never run ECC installers or hook scripts from a skill.

## Working agreements

- Keep changes minimal and reversible; a published gabarit version or
  migration is never rewritten destructively.
- Measure, don't assert: run the relevant benches/tests and report raw
  results (this project's docs distinguish established / demonstrated /
  unmeasured — keep that honesty).
- Before opening a PR: `ruff check .`, the marker-based selection above,
  `python3 scripts/audit_projet.py --rapide`, and `pnpm build` if the
  frontend changed.
