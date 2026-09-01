// Resolves whether a live SEAMTECH FastAPI backend is configured.
// Set SEAMTECH_API_URL (e.g. http://127.0.0.1:8000) to proxy real data.
// Optionally set SEAMTECH_AUTH_TOKEN to forward the X-SEAMTECH-TOKEN header.

export function backendBase(): string | null {
  const url = process.env.SEAMTECH_API_URL?.trim()
  return url ? url.replace(/\/$/, "") : null
}

export function authHeaders(): Record<string, string> {
  const token = process.env.SEAMTECH_AUTH_TOKEN?.trim()
  return token ? { "X-SEAMTECH-TOKEN": token } : {}
}
