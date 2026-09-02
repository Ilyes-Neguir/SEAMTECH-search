# Project structure

- config/: runtime and example configuration files
- data/: generated runtime data, backups, and local SQLite fallback index
- logs/: application logs
- sample_data/: sample content for local development
- scripts/: setup, launch, indexing, backup, and restore scripts
- seamtech_search/: Python package containing the crawler, indexer, API and CLI.
  `seamtech_search/static/` is a zero-dependency built-in browser UI (plain
  HTML/JS, no Node.js) served at `/` — used by the native single-Windows-host
  deployment (`SEAMTECH Search.cmd`). Kept intentionally alongside `frontend/`
  below, not a duplicate: it's the UI for the topology that has no Node.js.
- frontend/: Next.js web UI. Talks to the backend only through its own
  server-side `app/api/*` routes (see `frontend/lib/backend.ts`), which proxy
  to `SEAMTECH_API_URL`/`SEAMTECH_AUTH_TOKEN` — the FastAPI backend needs no
  CORS configuration and its URL/token are never exposed to the browser.
  Falls back to bundled sample data when `SEAMTECH_API_URL` is unset, so it
  runs standalone for UI development.
- tests/: regression tests
- Dockerfile: container image for the FastAPI app
- frontend/Dockerfile: multi-stage container image for the Next.js frontend
  (pnpm install → `next build` with `output: "standalone"` → minimal runtime)
- docker-compose.yml: PostgreSQL, the FastAPI backend, and the frontend —
  a full production-style stack in one command
- SEAMTECH Search.cmd: Windows launcher used by the Desktop shortcut
