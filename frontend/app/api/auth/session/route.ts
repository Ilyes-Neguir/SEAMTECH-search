import { NextResponse } from "next/server"
import { isAuthConfigured, isAuthenticated } from "@/lib/auth"

export const dynamic = "force-dynamic"

/**
 * Session state for the UI. Public on purpose — it only says whether the
 * caller holds a valid session and whether the server has a password
 * configured. It never echoes the token, the password, or backend data.
 */
export async function GET() {
  const authenticated = await isAuthenticated()
  return NextResponse.json(
    { authenticated, configured: isAuthConfigured() },
    { status: 200, headers: { "Cache-Control": "no-store" } },
  )
}
