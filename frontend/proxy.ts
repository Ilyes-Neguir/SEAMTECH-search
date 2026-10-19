import { NextResponse, type NextRequest } from "next/server"

/**
 * Next.js 16 proxy (the `middleware.ts` convention is deprecated).
 *
 * Two jobs, both inside the Lot 0a perimeter:
 *
 * 1. **CSRF double-submit** on every mutating request authenticated by the
 *    session cookie (POST/PUT/PATCH/DELETE under /api/*). The login endpoint is
 *    exempt because there is no session yet (and it must overwrite a stale
 *    cookie). `/api/auth/logout` and `/api/auth/logout-all` are NOT exempt:
 *    that is precisely where a cross-site POST would hurt.
 *
 *    Cookie `seamtech_csrf` is random, SameSite=Lax, readable by JavaScript;
 *    the client must echo it in `X-CSRF-Token`. Another origin cannot read the
 *    cookie, so it cannot forge the header. A missing/mismatched pair is 403.
 *
 * 2. **Cache-Control: no-store** on every response that belongs to a signed-in
 *    browser (session cookie present), including bfcache-defeating HTML. Data
 *    routes also set it themselves; this is the belt-and-braces layer.
 *
 * The proxy does NOT decide whether a session is valid: that requires Redis,
 * and it is enforced in every route handler via `requireAuthValide()` /
 * `/api/auth/session`. A stolen-but-revoked cookie still gets 401 there.
 */

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"])
const CSRF_EXEMPT = new Set(["/api/auth/login"])
const SESSION_COOKIE = "seamtech_session"
const CSRF_COOKIE = "seamtech_csrf"
const CSRF_HEADER = "x-csrf-token"

export function proxy(request: NextRequest) {
  const path = request.nextUrl.pathname
  const sessionCookie = request.cookies.get(SESSION_COOKIE)?.value

  if (
    sessionCookie &&
    path.startsWith("/api/") &&
    MUTATING.has(request.method) &&
    !CSRF_EXEMPT.has(path)
  ) {
    const cookieToken = request.cookies.get(CSRF_COOKIE)?.value ?? ""
    const headerToken = request.headers.get(CSRF_HEADER) ?? ""
    if (!cookieToken || !headerToken || cookieToken !== headerToken) {
      return NextResponse.json(
        { detail: "Jeton CSRF absent ou invalide : requête refusée." },
        { status: 403, headers: { "Cache-Control": "no-store", Vary: "Cookie" } },
      )
    }
  }

  const response = NextResponse.next()
  if (sessionCookie) {
    response.headers.set("Cache-Control", "no-store")
    response.headers.set("Vary", "Cookie")
  }
  return response
}

export const config = {
  matcher: [
    "/api/:path*",
    // Pages HTML : no-store pour les réponses authentifiées (le CSRF ne
    // s'applique qu'aux chemins /api/* ci-dessus). Les ressources statiques
    // sont exclues pour ne pas casser le build/chargement.
    "/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:png|jpg|jpeg|svg|ico|css|js|woff|woff2)$).*)",
  ],
}
