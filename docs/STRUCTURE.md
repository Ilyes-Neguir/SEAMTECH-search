# Project structure

- config/: runtime and example configuration files
- data/: generated runtime data, backups, and local SQLite fallback index
- logs/: application logs
- sample_data/: sample content for local development
- scripts/: setup, launch, indexing, backup, and restore scripts
- seamtech_search/: Python package containing the crawler, indexer, API and CLI.
- frontend/: the only web UI, used by both Docker and the native desktop launcher.
  Talks to the backend only through its own
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
