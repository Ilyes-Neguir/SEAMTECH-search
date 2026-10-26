import { type NextRequest, NextResponse } from "next/server"
import { CSRF_COOKIE, MODE_NOMINATIF, SESSION_COOKIE, currentSession, shouldMarkCookieSecure } from "@/lib/auth"
import { authHeaders, backendBase } from "@/lib/backend"

export const dynamic = "force-dynamic"

/**
 * "Sign out everywhere" for a nominative account.
 *
 * The backend first proves that the calling session is alive, then revokes
 * every PostgreSQL + Redis session belonging to that user — including the
 * calling one. Only after that server-side revocation do we clear this
 * browser's cookies. If the backend cannot confirm the revocation we still
 * clear the local cookies (the human asked to leave this browser), but we
 * answer 502 instead of pretending every device was disconnected.
 *
 * The rescue account has no database/Redis session, so only its local cookies
 * can be cleared; that documented limitation is unchanged.
 */
export async function POST(req: NextRequest) {
  const session = await currentSession()
  const base = backendBase()
  let status = 200
  let body: Record<string, unknown> = { ok: true }

  if (session?.mode === MODE_NOMINATIF) {
    if (!base || session.idSession === null || !session.jeton) {
      status = 502
      body = { ok: false, detail: "Backend non configuré : révocation globale impossible." }
    } else {
      try {
        const res = await fetch(`${base}/auth/deconnexion-tout`, {
          method: "POST",
          headers: {
            ...authHeaders(),
            "X-SEAMTECH-SESSION": String(session.idSession),
            "X-SEAMTECH-SESSION-JETON": session.jeton,
          },
          cache: "no-store",
        })
        if (!res.ok) {
          status = res.status === 401 ? 401 : 502
          body = {
            ok: false,
            detail: "Révocation globale refusée par le backend ; ce navigateur a été déconnecté localement.",
          }
          console.error(`[auth] Révocation globale refusée par le backend (${res.status}).`)
        }
      } catch {
        status = 502
        body = {
          ok: false,
          detail: "Backend injoignable ; ce navigateur a été déconnecté localement, pas les autres appareils.",
        }
        console.error("[auth] Backend injoignable pendant la révocation globale.")
      }
    }
  }

  const response = NextResponse.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Clear-Site-Data": '"cache", "cookies", "storage"',
    },
  })
  const secure = shouldMarkCookieSecure(req.headers.get("x-forwarded-proto"))
  response.cookies.set(SESSION_COOKIE, "", {
    httpOnly: true,
    sameSite: "lax",
    secure,
    path: "/",
    maxAge: 0,
  })
  response.cookies.set(CSRF_COOKIE, "", {
    httpOnly: false,
    sameSite: "lax",
    secure,
    path: "/",
    maxAge: 0,
  })
  return response
}
