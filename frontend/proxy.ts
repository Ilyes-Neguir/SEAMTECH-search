import { NextResponse, type NextRequest } from "next/server"
import { CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE } from "./lib/security-cookies"

// Lot 0a — CSRF double-submit for browser-authenticated API mutations.
//
// The session cookie is httpOnly, but cookie authentication alone still needs
// CSRF protection: SameSite=Lax is a useful browser default, not the security
// boundary. At login we also set a random, non-httpOnly `seamtech_csrf` cookie.
// Same-origin JS mirrors it into `X-CSRF-Token`; this proxy refuses mutating
// /api requests whose header does not match the cookie.
//
// Scope choices are deliberate:
//   * login is exempt because no CSRF cookie exists before authentication;
//   * requests without a session cookie pass through so route handlers keep
//     returning their normal 401 instead of a misleading CSRF error;
//   * safe methods are not checked.
const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"])
const LOGIN_PATH = "/api/auth/login"

export default function proxy(request: NextRequest) {
  if (!MUTATING_METHODS.has(request.method) || request.nextUrl.pathname === LOGIN_PATH) {
    return NextResponse.next()
  }

  if (!request.cookies.has(SESSION_COOKIE)) {
    return NextResponse.next()
  }

  const cookieToken = request.cookies.get(CSRF_COOKIE)?.value ?? ""
  const headerToken = request.headers.get(CSRF_HEADER) ?? ""
  if (cookieToken.length < 32 || headerToken.length < 32 || headerToken !== cookieToken) {
    return NextResponse.json(
      { detail: "Jeton CSRF invalide ou absent." },
      { status: 403, headers: { "Cache-Control": "no-store" } },
    )
  }

  return NextResponse.next()
}

export const config = {
  matcher: "/api/:path*",
}
