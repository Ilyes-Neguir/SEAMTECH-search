# SEAMTECH Search — Frontend

Next.js web UI for [SEAMTECH Search](../README.md). This app never talks to the
FastAPI backend from the browser — every request goes through this app's own
`app/api/*` route handlers (see `lib/backend.ts`), which proxy server-side to
`SEAMTECH_API_URL`. That means the backend needs no CORS configuration, and
its URL/auth token are never exposed to the browser.

If `SEAMTECH_API_URL` is unset, the UI falls back to bundled sample data
(`lib/sample-data.ts`) — useful for UI development without a running backend.

## Local development

```bash
pnpm install
pnpm dev
```

Open http://localhost:3000. By default this uses sample data. To point it at
a real backend running locally:

```bash
SEAMTECH_API_URL=http://127.0.0.1:8000 \
SEAMTECH_AUTH_TOKEN=your-token \
pnpm dev
```

## Production build

```bash
pnpm build
pnpm start
```

`next.config.mjs` sets `output: "standalone"`, so `pnpm build` also produces
a minimal, self-contained server under `.next/standalone/` — this is what
`Dockerfile` packages into the runtime image (see the root
[`docker-compose.yml`](../docker-compose.yml) for how the frontend and
backend containers are wired together).

## Environment variables

| Variable              | Where it's read           | Purpose                                                              |
| ---------------------- | -------------------------- | ---------------------------------------------------------------------- |
| `SEAMTECH_API_URL`     | server-side (`lib/backend.ts`) | Base URL of the FastAPI backend, e.g. `http://web:8000` in Docker. Unset = sample data. |
| `SEAMTECH_AUTH_TOKEN`  | server-side (`lib/backend.ts`) | Forwarded as `X-SEAMTECH-TOKEN` to the backend. Required if the backend has `auth_token` configured. |

Both are server-only (no `NEXT_PUBLIC_` prefix) and are never sent to the
browser.
