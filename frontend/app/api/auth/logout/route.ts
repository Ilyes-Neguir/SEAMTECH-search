import { type NextRequest, NextResponse } from "next/server"
import {
  CSRF_COOKIE,
  SESSION_COOKIE,
  csrfCookieOptions,
  currentSession,
  sessionCookieOptions,
  shouldMarkCookieSecure,
} from "@/lib/auth"
import { authHeaders, backendBase } from "@/lib/backend"

export const dynamic = "force-dynamic"

/**
 * Sign out — POST ONLY.
 *
 * `GET /api/auth/logout` intentionally has no handler: Next answers 405. The
 * old navigation link used GET, which is exactly the reported bug (« cliquer
 * Déconnexion → HTTP 405, cookie jamais supprimé »). A GET must never mutate
 * state, and a link must never be the logout mechanism (CSRF / prefetch).
 *
 * Order of operations:
 *   1. revoke the `sid` in Redis via the backend (the authority);
 *   2. clear BOTH cookies with the exact attributes used when setting them
 *      (Path=/, HttpOnly, SameSite=Lax, Secure when behind TLS);
 *   3. send `Clear-Site-Data: "cache", "cookies", "storage"`.
 *
 * If the backend is unreachable we still clear the local cookies and say so in
 * the JSON (`revoquee: false`) — the operator is signed out of THIS browser,
 * and the server-side session will die by TTL. We never pretend a revocation
 * happened when it did not.
 */
/**
 * GET is refused with an explicit 405 and an `Allow` header.
 *
 * Next would answer 405 anyway (no GET export), but a deterministic handler
 * makes the contract testable and tells a human/proxy exactly what is allowed.
 * A GET must never mutate state: prefetchers, link scanners and <img> tags all
 * issue GETs, and a logout must never be triggerable by one.
 */
export function GET() {
  return NextResponse.json(
    { detail: "Déconnexion : méthode POST uniquement." },
    { status: 405, headers: { Allow: "POST", "Cache-Control": "no-store" } },
  )
}

export async function POST(req: NextRequest) {
  const session = await currentSession()
  const base = backendBase()
  const secure = shouldMarkCookieSecure(req.headers.get("x-forwarded-proto"))
  let revoquee = false

  if (session?.sid && base) {
    try {
      const res = await fetch(`${base}/auth/deconnexion`, {
        method: "POST",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ sid: session.sid }),
        cache: "no-store",
      })
      if (res.ok) {
        revoquee = true
      } else {
        console.error(`[auth] Révocation de session refusée par le backend (${res.status}).`)
      }
    } catch {
      console.error("[auth] Backend injoignable : session non révoquée côté serveur (cookie effacé quand même).")
    }
  }

  const response = NextResponse.json(
    { ok: true, revoquee },
    {
      status: 200,
      headers: {
        "Cache-Control": "no-store",
        Vary: "Cookie",
        "Clear-Site-Data": '"cache", "cookies", "storage"',
      },
    },
  )
  // Mêmes attributs qu'à la pose : un effacement avec un Path/SameSite
  // différent laisserait le cookie en place.
  response.cookies.set(SESSION_COOKIE, "", sessionCookieOptions(0, secure))
  response.cookies.set(CSRF_COOKIE, "", csrfCookieOptions(0, secure))
  return response
}
