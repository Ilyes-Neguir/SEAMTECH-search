import { type NextRequest, NextResponse } from "next/server"
import { MODE_NOMINATIF, SESSION_COOKIE, currentSession, shouldMarkCookieSecure } from "@/lib/auth"
import { authHeaders, backendBase } from "@/lib/backend"

export const dynamic = "force-dynamic"

/**
 * Sign out.
 *
 * For a NOMINATIVE session this does two things, in this order: it asks the
 * backend to set `revoque_le` on the session row, THEN it clears the cookie.
 * Just clearing the cookie would be a lie — anyone who had copied the cookie
 * would keep a working session. Clearing it afterwards means a failed backend
 * call still signs the local user out.
 *
 * The rescue session has no database row, so only the cookie is cleared.
 * Not behind requireAuthValide(): signing out while already signed out must succeed.
 */
export async function POST(req: NextRequest) {
  const session = await currentSession()
  const base = backendBase()

  if (session && session.mode === MODE_NOMINATIF && session.idSession !== null && session.jeton && base) {
    try {
      const res = await fetch(`${base}/auth/deconnexion`, {
        method: "POST",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ id_session: session.idSession, jeton_session: session.jeton }),
        cache: "no-store",
      })
      if (!res.ok) {
        console.error(`[auth] Révocation de session refusée par le backend (${res.status}).`)
      }
    } catch {
      console.error("[auth] Backend injoignable : session non révoquée côté serveur (cookie effacé quand même).")
    }
  }

  const response = NextResponse.json({ ok: true }, { status: 200, headers: { "Cache-Control": "no-store" } })
  response.cookies.set(SESSION_COOKIE, "", {
    httpOnly: true,
    sameSite: "lax",
    secure: shouldMarkCookieSecure(req.headers.get("x-forwarded-proto")),
    path: "/",
    maxAge: 0,
  })
  return response
}
