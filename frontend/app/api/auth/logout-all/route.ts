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

const SANS_CACHE = {
  "Cache-Control": "no-store",
  Vary: "Cookie",
  "Clear-Site-Data": '"cache", "cookies", "storage"',
} as const

/**
 * « Déconnecter tous mes appareils » — POST ONLY.
 *
 * The backend revokes every Redis session of the authenticated user (and the
 * matching SQL audit rows). Each of those sessions is then rejected with 401
 * on every gated route, including on other devices.
 *
 * Redis unavailable → 503. The local cookies are still cleared (this browser
 * must not stay signed in), but the response does NOT claim the other devices
 * were revoked.
 */
export async function POST(req: NextRequest) {
  const session = await currentSession()
  const base = backendBase()
  const secure = shouldMarkCookieSecure(req.headers.get("x-forwarded-proto"))

  const effacerCookies = (reponse: NextResponse): NextResponse => {
    reponse.cookies.set(SESSION_COOKIE, "", sessionCookieOptions(0, secure))
    reponse.cookies.set(CSRF_COOKIE, "", csrfCookieOptions(0, secure))
    return reponse
  }

  if (!session?.sid) {
    // Déjà déconnecté : l'action reste sûre et idempotente.
    return effacerCookies(
      NextResponse.json({ ok: true, sessions_revoquees: 0 }, { status: 200, headers: SANS_CACHE }),
    )
  }
  if (!base) {
    return effacerCookies(
      NextResponse.json(
        {
          detail: "Backend non configuré (SEAMTECH_API_URL) : révocation globale impossible (fail-closed).",
          sessions_revoquees: 0,
        },
        { status: 503, headers: SANS_CACHE },
      ),
    )
  }

  let res: Response
  try {
    res = await fetch(`${base}/auth/deconnexion-tout`, {
      method: "POST",
      headers: { ...authHeaders(), "X-SEAMTECH-SESSION-SID": session.sid },
      cache: "no-store",
    })
  } catch {
    return effacerCookies(
      NextResponse.json(
        { detail: "Backend injoignable : révocation globale impossible (fail-closed).", sessions_revoquees: 0 },
        { status: 503, headers: SANS_CACHE },
      ),
    )
  }

  const corps = (await res.json().catch(() => ({}))) as Record<string, unknown>
  if (!res.ok) {
    return effacerCookies(
      NextResponse.json(
        {
          detail:
            typeof corps.detail === "string"
              ? corps.detail
              : "Magasin de sessions indisponible (Redis) : révocation globale impossible (fail-closed).",
          sessions_revoquees: 0,
        },
        { status: res.status === 401 ? 401 : 503, headers: SANS_CACHE },
      ),
    )
  }
  return effacerCookies(
    NextResponse.json(
      {
        ok: true,
        sessions_revoquees: Number(corps.sessions_revoquees ?? 0),
        sessions_sql_revoquees: Number(corps.sessions_sql_revoquees ?? 0),
      },
      { status: 200, headers: SANS_CACHE },
    ),
  )
}
