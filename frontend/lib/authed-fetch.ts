// Client-side fetch wrapper that understands the session gate and CSRF.
//
// Every app/api/* route (except /api/health's liveness payload and the
// /api/auth/* endpoints) now returns 401 when the httpOnly session cookie is
// missing or expired. Without this wrapper an expired session mid-use showed
// up as "Search request failed." with no way back — the operator had to know
// to reload. This turns any 401 into a redirect to the sign-in screen and
// preserves where they were.
//
// Lot 0a adds the double-submit CSRF token: the login route sets a
// non-httpOnly `seamtech_csrf` cookie, and every mutating request mirrors it
// into `X-CSRF-Token`. The Next proxy compares the two before a route handler
// runs. The token is not a credential — it only proves that same-origin JS
// initiated the request.
//
// Only import this from client components ("use client"); it touches window.

import { CSRF_COOKIE, CSRF_HEADER } from "@/lib/security-cookies"

const LOGIN_PATH = "/login"
const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"])

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

/** Read the double-submit CSRF token from the browser cookie jar. */
export function csrfHeaders(): Record<string, string> {
  if (typeof document === "undefined") return {}
  const prefix = `${CSRF_COOKIE}=`
  const part = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith(prefix))
  if (!part) return {}
  const token = decodeURIComponent(part.slice(prefix.length))
  return token ? { [CSRF_HEADER]: token } : {}
}

/** fetch() that adds CSRF on mutations and redirects to /login on 401. */
export async function authedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase()
  let effectiveInit = init

  if (MUTATING_METHODS.has(method)) {
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined))
    const token = csrfHeaders()[CSRF_HEADER]
    if (token && !headers.has(CSRF_HEADER)) headers.set(CSRF_HEADER, token)
    effectiveInit = { ...init, headers }
  }

  const response = await fetch(input, effectiveInit)
  if (response.status === 401) redirectToLogin()
  return response
}
