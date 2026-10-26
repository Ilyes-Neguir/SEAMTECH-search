# ECC skills vendored for SEAMTECH-search

Curated subset of [affaan-m/ECC](https://github.com/affaan-m/ECC) **v2.2.3**
(commit `90606c8315ee09cafc303515c5f33024db6b9882`, MIT — see
[`ECC-LICENSE`](ECC-LICENSE)) adapted so an AI coding agent working in this
repository (Arena, or any agent that reads `AGENTS.md`) can use ECC's
workflow skills without installing the full 311-skill ECC plugin.

## Layout

```text
AGENTS.md                  <- entry point: SEAMTECH rules + skill routing table
.agents/
  skills/<name>/SKILL.md   <- 30 curated ECC skills (Markdown only, verbatim)
  agents/<name>.md         <- 16 ECC reviewer/role prompts, used as checklists
  ecc-manifest.json        <- pinned inventory: ECC commit + SHA-256 per file
  ECC-LICENSE              <- upstream MIT licence (attribution)
scripts/sync_ecc_skills.py <- re-sync / verify tool (stdlib only)
tests/test_ecc_skills.py   <- guard: manifest, disk and AGENTS.md must agree
```

## How an agent uses this

1. Read the root [`AGENTS.md`](../AGENTS.md) first — SEAMTECH's own rules
   always win over an ECC skill on any conflict.
2. Classify the task, open the 1–3 matching `SKILL.md` files from the
   routing table in `AGENTS.md`, and follow them adapted to this repo
   (SEAMTECH commands, marker-based test selection, pinned dependencies).
3. For reviews, apply the matching checklist in `.agents/agents/` in-role:
   Arena cannot spawn ECC sub-agents, so the agent runs the checklist
   itself and says which skills/agents it used.

## Installed skills (30)

### Core workflow

| Skill | Use when |
|---|---|
| `tdd-workflow` | New feature, bug fix, refactor — failing test first, then green |
| `verification-loop` | Verifying a finished change / before a PR (build, types, lint, tests, security grep, diff review) |
| `search-first` | Before writing a custom helper — check the codebase / an existing tool first (subject to the SEAMTECH dependency policy) |
| `coding-standards` | Baseline naming, readability and code-quality conventions |
| `error-handling` | Typed errors, boundaries, retries, user-facing error messages (Python/TypeScript) |
| `git-workflow` | Branching, clean history, merge vs rebase, conflict handling |
| `github-ops` | Issues, PRs, CI and releases with the `gh` CLI |
| `production-audit` | Local-evidence production-readiness audit ("what breaks in prod?") |

### Python / FastAPI backend

| Skill | Use when |
|---|---|
| `python-patterns` | Writing or reviewing Python: idioms, PEP 8, type hints |
| `python-testing` | pytest tests: fixtures, mocks, parametrisation, coverage |
| `fastapi-patterns` | FastAPI structure, Pydantic v2, dependency injection, async handlers, auth, tests |
| `api-design` | Designing/reviewing REST endpoints: naming, status codes, pagination, errors, versioning |
| `postgres-patterns` | PostgreSQL schemas, indexes, query optimisation, security |
| `redis-patterns` | Redis structures, caching, locks, rate limiting, pub/sub, connection management |
| `database-migrations` | Safe reversible migrations: expand–contract, concurrent indexes, batched backfills |
| `docker-patterns` | Dockerfiles and Compose: security, networking, volumes, orchestration |
| `deployment-patterns` | CI/CD, health checks, rollback, production readiness |
| `security-review` | Auth, user input, file uploads, secrets, new endpoints, sensitive data |
| `regex-vs-llm-structured-text` | Parsing structured documents (forms, tables, fiches): hybrid regex-first extraction |
| `ai-regression-testing` | Regression strategies for AI-assisted changes; sandbox API testing |

### Next.js / React frontend

| Skill | Use when |
|---|---|
| `react-patterns` | React 18/19: hooks, server/client boundaries, Suspense, forms, data fetching |
| `react-testing` | Component tests with Testing Library + Vitest/Jest + MSW (only if/when such tests are added; today the frontend is covered by Playwright) |
| `react-performance` | React/Next.js performance optimisation patterns |
| `nextjs-turbopack` | Next.js 16+ / Turbopack: dev startup, HMR, bundler trade-offs, `proxy.ts` naming |
| `frontend-patterns` | React/Next.js UI patterns, state management, UI best practices |
| `frontend-a11y` | Accessible React/Next.js: semantics, ARIA, forms, keyboard, focus |
| `e2e-testing` | Playwright: page objects, CI integration, flaky-test strategies |

### Architecture / documentation

| Skill | Use when |
|---|---|
| `architecture-decision-records` | Recording an architectural decision as a numbered ADR |
| `documentation-lookup` | Preferring current library/framework docs over memory (in Arena: web search / fetch the official docs instead of Context7) |
| `contract-first` | Coordinating frontend ↔ backend work through one machine-checkable contract (OpenAPI) |

## Installed agent checklists (16)

`planner`, `architect`, `tdd-guide`, `code-reviewer`, `security-reviewer`,
`python-reviewer`, `fastapi-reviewer`, `database-reviewer`,
`typescript-reviewer`, `react-reviewer`, `performance-optimizer`,
`e2e-runner`, `doc-updater`, `build-error-resolver`, `refactor-cleaner`,
`rag-pipeline-reviewer`.

These are ECC sub-agent prompts kept as **review checklists**: e.g. after a
FastAPI change, run through `.agents/agents/fastapi-reviewer.md`; after a
search/retrieval change, `.agents/agents/rag-pipeline-reviewer.md`. Their
`tools:`/`model:` frontmatter targets Claude Code and is ignored here.

## What was deliberately NOT installed

- **~281 other ECC skills** for stacks/domains this repo does not use
  (Go, Rust, Swift, Java, PHP, healthcare, marketing, trading, homelab,
  Blender, Bengali NLP, …) — they would pollute skill routing.
- **Misleading look-alikes**: `backend-patterns` (Node/Express oriented —
  `fastapi-patterns` covers this stack), `benchmark-methodology`
  (competitor scoring, not performance), `security-scan` (audits a Claude
  Code `.claude/` directory), `iterative-retrieval` (sub-agent context
  retrieval, not product search).
- **All ECC hooks, MCP configs, commands and installers** — hooks execute
  code automatically on tool use, and MCP entries need external keys.
  Nothing vendored here can run by itself: `sync_ecc_skills.py` only ever
  copies `.md` files (plus the licence), and `tests/test_ecc_skills.py`
  fails if a non-Markdown file appears under `.agents/skills/` or
  `.agents/agents/`.
- **ECC `rules/` and ECC's own `AGENTS.md`** — their generic conventions
  (e.g. strict immutability, flat 80 % coverage) would conflict with
  SEAMTECH's established rules; the non-conflicting essentials are
  distilled into this repo's `AGENTS.md` instead.

## Arena adaptations (read skills through these)

- `documentation-lookup` assumes a Context7 MCP server. In Arena, honour
  its intent with web search / fetching official docs; do not try to
  install Context7.
- `verification-loop` is written for a TypeScript/Claude Code session.
  Map its phases to this repo: `ruff check .`, the marker-based pytest
  selection in `README.md`, `scripts/coverage_gate.py`, and
  `pnpm build` in `frontend/` when the frontend changed.
- `search-first` encourages adopting existing packages. Here any new
  dependency must pass `scripts/audit_dependency_policy.py` and stay
  pinned in sync (`requirements.txt` ↔ `pyproject.toml`,
  `requirements-dev.txt` ↔ `[project.optional-dependencies].dev`) — the
  repo's policy wins.
- `e2e-runner` mentions Vercel Agent Browser; this repo uses Playwright
  (`frontend/playwright.config.ts`, `pnpm test:e2e`).
- Commands pasted inside a skill are illustrations, not instructions to
  run verbatim — adapt them to this repository and its test markers.

## Updating

```bash
python3 scripts/sync_ecc_skills.py --check                 # offline: disk vs manifest
python3 scripts/sync_ecc_skills.py --list                  # the allowlist
python3 scripts/sync_ecc_skills.py --update                # re-sync pinned commit
python3 scripts/sync_ecc_skills.py --update --commit <sha> # move to a newer ECC commit
```

`--update` clones ECC, checks out the requested commit, copies only the
allowlisted Markdown, rewrites `ecc-manifest.json` with fresh SHA-256
pins, and warns about any locally modified vendored file it overwrites.
To add/drop a skill, edit `SKILLS`/`AGENTS` in
`scripts/sync_ecc_skills.py`, update the routing table in `AGENTS.md`
and the tables above, then run `--update` — `tests/test_ecc_skills.py`
fails until manifest, disk and `AGENTS.md` agree.
