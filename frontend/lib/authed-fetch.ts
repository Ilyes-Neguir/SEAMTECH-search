// Client-side fetch wrapper that understands the session gate.
//
// Every app/api/* route (except /api/health's liveness payload and the
// /api/auth/* endpoints) now returns 401 when the httpOnly session cookie is
// missing or expired. Without this wrapper an expired session mid-use showed
// up as "Search request failed." with no way back — the operator had to know
// to reload. This turns any 401 into a redirect to the sign-in screen and
// preserves where they were.
//
// Only import this from client components ("use client"); it touches window.

const LOGIN_PATH = "/login"

function isSafeNextPath(candidate: string): boolean {
  // Same-origin relative paths only. "//evil.com" and absolute URLs are
  // protocol-relative/absolute and must never be used as a redirect target.
  return candidate.startsWith("/") && !candidate.startsWith("//") && !candidate.includes("\\")
}

export function redirectToLogin(): void {
  if (typeof window === "undefined") return
  if (window.location.pathname.startsWith(LOGIN_PATH)) return
  const current = window.location.pathname + window.location.search
  const next = isSafeNextPath(current) ? `?next=${encodeURIComponent(current)}` : ""
  window.location.replace(`${LOGIN_PATH}${next}`)
}

/** Resolve the `?next=` target on the login page, falling back to "/". */
export function resolveNextPath(raw: string | null): string {
  if (!raw) return "/"
  return isSafeNextPath(raw) ? raw : "/"
}

/** fetch() that redirects to /login when the server says the session is gone. */
export async function authedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const response = await fetch(input, init)
  if (response.status === 401) redirectToLogin()
  return response
}
