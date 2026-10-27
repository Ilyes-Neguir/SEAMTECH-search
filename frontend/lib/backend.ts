// Resolves whether a live SEAMTECH FastAPI backend is configured.
// Set SEAMTECH_API_URL (e.g. http://127.0.0.1:8000) to proxy real data.
// Optionally set SEAMTECH_AUTH_TOKEN to forward the X-SEAMTECH-TOKEN header.

import { NextResponse } from "next/server"
import { MODE_NOMINATIF, SESSION_COOKIE, currentSession, isTokenValid } from "@/lib/auth"
import { cookies } from "next/headers"
import type { Role } from "@/lib/roles"

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

/** Canonical role of the signed-in human, read from the signed cookie. */
export async function currentRole(): Promise<Role | null> {
  return (await currentSession())?.role ?? null
}

/**
 * Defence-in-depth role gate for browser routes. It never replaces the
 * backend guard: the same signed identity/session headers are still forwarded
 * and FastAPI re-resolves the role from PostgreSQL for protected actions.
 */
export async function requireRolesServer(...roles: Role[]): Promise<NextResponse | null> {
  const denied = await requireAuthValide()
  if (denied) return denied
  const role = await currentRole()
  if (role && roles.includes(role)) return null
  return NextResponse.json(
    { detail: `Accès réservé au(x) rôle(s) ${roles.join(", ")}.` },
    { status: 403, headers: { "Cache-Control": "no-store" } },
  )
}

/** Like requireRolesServer, but rescue/shared sessions can never pass. */
/**
 * Relaie un multipart au backend sans jamais le tamponner dans Next.js.
 *
 * `req.formData()` lirait tout le corps avant que le backend puisse appliquer
 * ses propres bornes. Ici les octets traversent en flux, sont comptés au fil
 * de l'eau et le relais est interrompu dès que le plafond de la route est
 * franchi — y compris sans Content-Length fiable.
 */
export async function proxyMultipartBorne(
  req: Request,
  cheminBackend: string,
  maxOctets: number,
): Promise<NextResponse> {
  const base = backendBase()
  if (!base) {
    return NextResponse.json(
      { detail: "Backend non configuré (SEAMTECH_API_URL)." },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    )
  }
  const contentType = req.headers.get("content-type") ?? ""
  if (!contentType.toLowerCase().startsWith("multipart/form-data")) {
    return NextResponse.json(
      { detail: "Envoi multipart/form-data requis." },
      { status: 415, headers: { "Cache-Control": "no-store" } },
    )
  }
  const longueurDeclaree = req.headers.get("content-length")
  let longueur: number | null = null
  if (longueurDeclaree !== null) {
    longueur = Number(longueurDeclaree)
    if (!Number.isSafeInteger(longueur) || longueur < 0) {
      return NextResponse.json(
        { detail: "Content-Length invalide." },
        { status: 400, headers: { "Cache-Control": "no-store" } },
      )
    }
    if (longueur > maxOctets) {
      return NextResponse.json(
        { detail: `Envoi trop volumineux (${longueur} octets, maximum ${maxOctets}).` },
        { status: 413, headers: { "Cache-Control": "no-store" } },
      )
    }
  }
  if (!req.body) {
    return NextResponse.json(
      { detail: "Corps multipart absent." },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    )
  }

  let total = 0
  let tropGrand = false
  const corpsBorne = req.body.pipeThrough(
    new TransformStream<Uint8Array, Uint8Array>({
      transform(chunk, controller) {
        total += chunk.byteLength
        if (total > maxOctets) {
          tropGrand = true
          controller.error(new Error("Envoi multipart trop volumineux"))
          return
        }
        controller.enqueue(chunk)
      },
    }),
  )
  const extra: Record<string, string> = { "content-type": contentType }
  if (longueur !== null) extra["content-length"] = String(longueur)
  try {
    const response = await fetch(`${base}${cheminBackend}`, {
      method: "POST",
      headers: await backendHeaders(extra),
      body: corpsBorne,
      cache: "no-store",
      duplex: "half",
    } as RequestInit & { duplex: "half" })
    const typeReponse = response.headers.get("content-type")
    return new NextResponse(response.body, {
      status: response.status,
      headers: {
        "Cache-Control": "no-store",
        ...(typeReponse ? { "content-type": typeReponse } : {}),
      },
    })
  } catch {
    if (tropGrand) {
      return NextResponse.json(
        { detail: `Envoi trop volumineux (maximum ${maxOctets} octets).` },
        { status: 413, headers: { "Cache-Control": "no-store" } },
      )
    }
    console.error("[proxy] Backend SEAMTECH injoignable pendant un envoi multipart.")
    return NextResponse.json(
      { detail: "Backend SEAMTECH injoignable." },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    )
  }
}

export async function requireNominativeRoles(...roles: Role[]): Promise<NextResponse | null> {
  const denied = await requireAuthValide()
  if (denied) return denied
  const session = await currentSession()
  if (!session || session.mode !== MODE_NOMINATIF || !roles.includes(session.role)) {
    return NextResponse.json(
      {
        detail:
          "Cette action exige un compte nominatif avec un rôle autorisé ; le compte de secours ne peut pas l'exécuter.",
      },
      { status: 403, headers: { "Cache-Control": "no-store" } },
    )
  }
  return null
}
