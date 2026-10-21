// Client-side half of the CSRF double-submit pattern (Lot 0a).
//
// The server sets a random, readable `seamtech_csrf` cookie at login. Every
// mutating request must echo it in `X-CSRF-Token`; `proxy.ts` rejects a
// missing/mismatched pair with 403. A cross-site page can make the browser
// send the cookie, but it cannot READ it, so it cannot forge the header.
//
// Only import this from client components ("use client"); it touches document.

export const CSRF_COOKIE = "seamtech_csrf"
export const CSRF_HEADER = "X-CSRF-Token"

function lireCookie(nom: string): string | null {
  if (typeof document === "undefined") return null
  const motif = new RegExp(`(?:^|;\\s*)${nom.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}=([^;]*)`)
  const correspondance = document.cookie.match(motif)
  if (!correspondance) return null
  try {
    return decodeURIComponent(correspondance[1])
  } catch {
    return correspondance[1]
  }
}

/** The current CSRF token, or null when no session/CSRF cookie exists. */
export function csrfToken(): string | null {
  return lireCookie(CSRF_COOKIE)
}

/** Header object to merge into a mutating fetch. Empty for safe methods. */
export function csrfHeaders(method: string = "POST"): Record<string, string> {
  if (["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase())) return {}
  const token = csrfToken()
  return token ? { [CSRF_HEADER]: token } : {}
}

/**
 * Return `init` with the CSRF header added for POST/PUT/PATCH/DELETE.
 * Existing headers (including Content-Type for JSON) are preserved.
 */
export function withCsrf(init: RequestInit = {}): RequestInit {
  const method = (init.method ?? "GET").toUpperCase()
  const ajouts = csrfHeaders(method)
  if (Object.keys(ajouts).length === 0) return init
  const headers = new Headers(init.headers)
  for (const [cle, valeur] of Object.entries(ajouts)) headers.set(cle, valeur)
  return { ...init, headers }
}
