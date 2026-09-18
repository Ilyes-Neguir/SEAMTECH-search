import { type NextRequest, NextResponse } from "next/server"
import { SESSION_COOKIE, shouldMarkCookieSecure } from "@/lib/auth"

export const dynamic = "force-dynamic"

/**
 * Clear the session cookie. Not behind requireAuth(): logging out while
 * already signed out must still succeed so the UI can recover from an
 * expired session without a redirect loop.
 */
export async function POST(req: NextRequest) {
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
