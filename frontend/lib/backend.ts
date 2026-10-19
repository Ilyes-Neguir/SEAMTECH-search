// Resolves whether a live SEAMTECH FastAPI backend is configured.
// Set SEAMTECH_API_URL (e.g. http://127.0.0.1:8000) to proxy real data.
// Optionally set SEAMTECH_AUTH_TOKEN to forward the X-SEAMTECH-TOKEN header.

import { NextResponse } from "next/server"
import { SESSION_COOKIE, currentSession } from "@/lib/auth"
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
 * The Redis `sid` travels too. It is what lets /auth/session answer 401 after
 * a logout even though the cookie is still signed.
 */
export async function backendHeaders(extra?: Record<string, string>): Promise<Record<string, string>> {
  const headers: Record<string, string> = { ...authHeaders(), ...(extra ?? {}) }
  const session = await currentSession()
  if (!session) return headers
  headers["X-SEAMTECH-UTILISATEUR"] = session.identifiant
  headers["X-SEAMTECH-ROLE"] = session.role
  headers["X-SEAMTECH-SESSION-SID"] = session.sid
  // Le cookie ne porte AUCUN secret de session historique : l'identité est
  // relue en base à partir du sid, jamais transportée depuis le navigateur.
  return headers
}

/** Why a server-side session check failed — 503 ≠ 401, and the UI must say so. */
export type EtatSession = "vivante" | "invalide" | "indisponible"

export type ReponseSessionBackend = {
  status: number
  body: Record<string, unknown>
}

/**
 * Ask the backend (which asks Redis) whether `sid` is still alive.
 *
 * The backend is the source of truth: a signed cookie with a revoked, absent,
 * idle-expired or absolutely-expired sid must NOT keep anyone signed in. A
 * backend we cannot reach is reported as `indisponible` (→ 503), never as a
 * valid session: failing open here would mean serving a revoked session for as
 * long as Redis or the backend is down.
 */
export async function sessionBackend(sid: string): Promise<ReponseSessionBackend | null> {
  const base = backendBase()
  if (!base) return null
  const res = await fetch(`${base}/auth/session`, {
    headers: { ...authHeaders(), "X-SEAMTECH-SESSION-SID": sid },
    cache: "no-store",
  })
  const body = (await res.json().catch(() => ({}))) as Record<string, unknown>
  return { status: res.status, body }
}

export async function etatSessionServeur(): Promise<EtatSession> {
  const session = await currentSession()
  if (!session) return "invalide"
  try {
    const reponse = await sessionBackend(session.sid)
    if (!reponse) return "indisponible"
    if (reponse.status === 200) return "vivante"
    if (reponse.status === 503) return "indisponible"
    return "invalide"
  } catch {
    console.error("[auth] Backend injoignable : session non confirmée, requête refusée.")
    return "indisponible"
  }
}

/** Is the session behind this cookie still ALIVE on the backend? */
export async function sessionServeurVivante(): Promise<boolean> {
  return (await etatSessionServeur()) === "vivante"
}

/**
 * Gate for app/api/* route handlers — the LOT L.2/Lot 0a replacement for
 * `requireAuth`.
 *
 * Same contract (`null` when allowed, a response to return as-is), but the
 * session is confirmed with Redis via the backend. This is what makes
 * `POST /auth/logout` effective everywhere: reusing a copied cookie after
 * signing out now gets 401 from EVERY route, not just from /api/auth/session.
 *
 * Redis/backend unavailable → explicit 503 (fail-closed), never 401: the
 * operator must be able to tell a dead session from a dead session store.
 */
export async function requireAuthValide(): Promise<NextResponse | null> {
  const store = await cookies()
  const cookie = store.get(SESSION_COOKIE)?.value
  const session = await currentSession()
  if (!session || !cookie) {
    return NextResponse.json(
      { detail: "Authentication required. Sign in at /login." },
      { status: 401, headers: { "Cache-Control": "no-store" } },
    )
  }
  const etat = await etatSessionServeur()
  if (etat === "vivante") return null
  if (etat === "indisponible") {
    return NextResponse.json(
      {
        detail:
          "Magasin de sessions indisponible (Redis/backend) : la session ne peut pas être vérifiée. " +
          "Refus explicite (fail-closed).",
        reason: "session_store_indisponible",
      },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    )
  }
  return NextResponse.json(
    { detail: "Session expirée ou révoquée. Reconnectez-vous.", reason: "session_invalide" },
    { status: 401, headers: { "Cache-Control": "no-store" } },
  )
}
