import { type NextRequest, NextResponse } from "next/server"
import {
  SESSION_COOKIE,
  clientIdentifier,
  cookieMaxAgeSeconds,
  createSessionToken,
  clearFailedAttempts,
  isAuthConfigured,
  lockoutRemaining,
  noteFailedAttempt,
  passwordMatches,
  shouldMarkCookieSecure,
  uiPassword,
} from "@/lib/auth"

export const dynamic = "force-dynamic"

/**
 * Exchange the shared operator password for an httpOnly session cookie.
 *
 * Deliberately NOT behind requireAuth() — it is the route that creates the
 * session. Every other route in app/api/* is gated.
 */
export async function POST(req: NextRequest) {
  if (!isAuthConfigured()) {
    // Fail closed: no password configured means nobody gets in, rather than
    // everybody getting in.
    console.error("[auth] SEAMTECH_UI_PASSWORD is not set; login is disabled.")
    return NextResponse.json(
      {
        detail:
          "Authentication is not configured on this server. Set SEAMTECH_UI_PASSWORD (and SEAMTECH_SESSION_SECRET) and restart the frontend.",
      },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    )
  }

  const identifier = clientIdentifier(req.headers.get("x-forwarded-for"), req.headers.get("x-real-ip"))
  const lockedFor = lockoutRemaining(identifier)
  if (lockedFor > 0) {
    return NextResponse.json(
      { detail: `Too many failed sign-in attempts. Try again in ${Math.ceil(lockedFor / 1000)}s.` },
      { status: 429, headers: { "Cache-Control": "no-store", "Retry-After": String(Math.ceil(lockedFor / 1000)) } },
    )
  }

  // Accept JSON (the login form) and urlencoded form posts (a plain HTML
  // submission or a curl probe) so the endpoint behaves the same either way.
  let password = ""
  const contentType = req.headers.get("content-type") ?? ""
  try {
    if (contentType.includes("application/json")) {
      const body = await req.json().catch(() => null)
      password = typeof body?.password === "string" ? body.password : ""
    } else {
      const form = await req.formData().catch(() => null)
      const value = form?.get("password")
      password = typeof value === "string" ? value : ""
    }
  } catch {
    password = ""
  }

  if (!passwordMatches(password, uiPassword())) {
    const attemptsLeft = noteFailedAttempt(identifier)
    return NextResponse.json(
      { detail: attemptsLeft > 0 ? `Incorrect password. ${attemptsLeft} attempt(s) remaining.` : "Incorrect password." },
      { status: 401, headers: { "Cache-Control": "no-store" } },
    )
  }

  clearFailedAttempts(identifier)
  const response = NextResponse.json({ ok: true, redirectTo: "/" }, { status: 200, headers: { "Cache-Control": "no-store" } })
  response.cookies.set(SESSION_COOKIE, createSessionToken(), {
    httpOnly: true,
    sameSite: "lax",
    secure: shouldMarkCookieSecure(req.headers.get("x-forwarded-proto")),
    path: "/",
    maxAge: cookieMaxAgeSeconds(),
  })
  return response
}
