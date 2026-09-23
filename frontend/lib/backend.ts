// Resolves whether a live SEAMTECH FastAPI backend is configured.
// Set SEAMTECH_API_URL (e.g. http://127.0.0.1:8000) to proxy real data.
// Optionally set SEAMTECH_AUTH_TOKEN to forward the X-SEAMTECH-TOKEN header.

import { NextResponse } from "next/server"
import { MODE_NOMINATIF, SESSION_COOKIE, currentSession, isTokenValid } from "@/lib/auth"
import { cookies } from "next/headers"

export function backendBase(): string | null {
  const url = process.env.SEAMTECH_API_URL?.trim()
  return url ? url.replace(/\/$/, "") : null
}

export function authHeaders(): Record<string, string> {
  const token = process.env.SEAMTECH_AUTH_TOKEN?.trim()
  return token ? { "X-SEAMTECH-TOKEN": token } : {}
}

/**
 * Headers for a backend call made on behalf of the signed-in human.
 *
 * The attribution headers are recomputed from the SIGNED cookie on every call
 * — never from the browser's request body or query string, both of which the
 * user controls. The backend honours them only when X-SEAMTECH-TOKEN is valid
 * on the same request, so a browser that learned these two header names still
 * cannot forge an identity: it does not have the service token.
 *
 * The session headers (id + token) travel too, which is what lets /auth/session
 * answer 401 after a logout even though the cookie is still signed.
 */
export async function backendHeaders(extra?: Record<string, string>): Promise<Record<string, string>> {
  const headers: Record<string, string> = { ...authHeaders(), ...(extra ?? {}) }
  const session = await currentSession()
  if (!session) return headers
  headers["X-SEAMTECH-UTILISATEUR"] = session.identifiant
  headers["X-SEAMTECH-ROLE"] = session.role
  if (session.idSession !== null && session.jeton) {
    headers["X-SEAMTECH-SESSION"] = String(session.idSession)
    headers["X-SEAMTECH-SESSION-JETON"] = session.jeton
  }
  return headers
}

/**
 * Is the session behind this cookie still ALIVE on the backend?
 *
 * Signature validity is not enough: a logout (or an administrator disabling an
 * account) sets `revoque_le` on the `session_ui` row, and the cookie stays
 * cryptographically valid until it expires. Every data route therefore asks the
 * backend — the source of truth — before serving anything. The rescue account
 * has no database row, so only its signature is checked (documented limitation).
 *
 * A backend we cannot reach is treated as "not alive": failing open here would
 * mean serving a revoked session for as long as the backend is down.
 */
export async function sessionServeurVivante(): Promise<boolean> {
  const session = await currentSession()
  if (!session) return false
  if (session.mode !== MODE_NOMINATIF) return true

  const base = backendBase()
  if (!base || session.idSession === null || !session.jeton) return false
  try {
    const res = await fetch(`${base}/auth/session`, {
      headers: {
        ...authHeaders(),
        "X-SEAMTECH-SESSION": String(session.idSession),
        "X-SEAMTECH-SESSION-JETON": session.jeton,
      },
      cache: "no-store",
    })
    return res.ok
  } catch {
    console.error("[auth] Backend injoignable : session non confirmée, requête refusée.")
    return false
  }
}

/**
 * Gate for app/api/* route handlers — the LOT L.2 replacement for `requireAuth`.
 *
 * Same contract (`null` when allowed, a 401 response to return as-is), but the
 * session is confirmed with the backend. This is what makes `POST /auth/logout`
 * effective everywhere: reusing a copied cookie after signing out now gets 401
 * from EVERY route, not just from /api/auth/session.
 */
export async function requireAuthValide(): Promise<NextResponse | null> {
  const store = await cookies()
  if (!isTokenValid(store.get(SESSION_COOKIE)?.value)) {
    return NextResponse.json(
      { detail: "Authentication required. Sign in at /login." },
      { status: 401, headers: { "Cache-Control": "no-store" } },
    )
  }
  if (await sessionServeurVivante()) return null
  return NextResponse.json(
    { detail: "Session expirée ou révoquée. Reconnectez-vous." },
    { status: 401, headers: { "Cache-Control": "no-store" } },
  )
}
