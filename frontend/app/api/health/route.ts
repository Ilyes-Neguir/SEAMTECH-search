import { NextResponse } from "next/server"
import { backendBase, authHeaders } from "@/lib/backend"
import { SAMPLE_STATS } from "@/lib/sample-data"
import type { HealthResponse } from "@/lib/types"
import { isAuthenticated } from "@/lib/auth"

export const dynamic = "force-dynamic"

/**
 * Health / liveness.
 *
 * This route is the one deliberate exception to the requireAuth() gate that
 * covers every other app/api/* route, because it is the target of the
 * docker-compose frontend healthcheck and the CI `curl -fsS .../api/health`
 * probe — a 401 there would mark a perfectly healthy container unhealthy.
 *
 * It is still not a hole: unauthenticated callers get a static liveness
 * payload only. Archive counts (documents/files/folders/last_scan) and any
 * call to the backend require a valid session, so the route cannot be used
 * to enumerate the index without signing in.
 */
export async function GET() {
  const authenticated = await isAuthenticated()

  if (!authenticated) {
    return NextResponse.json(
      { status: "ok", authenticated: false, detail: "Sign in at /login for index health details." },
      { status: 200, headers: { "Cache-Control": "no-store" } },
    )
  }

  const base = backendBase()
  if (base) {
    try {
      const res = await fetch(`${base}/health`, { headers: authHeaders(), cache: "no-store" })
      const body = await res.json()
      return NextResponse.json({ ...body, authenticated: true }, { status: res.status })
    } catch {
      return NextResponse.json({ status: "unreachable", authenticated: true }, { status: 502 })
    }
  }

  const demoMode = process.env.SEAMTECH_DEMO_MODE === "1"
  const isProd = process.env.NODE_ENV === "production"
  if (!demoMode || isProd) {
    return NextResponse.json(
      { detail: "Backend not configured. Set SEAMTECH_API_URL or enable SEAMTECH_DEMO_MODE=1 for demo." },
      { status: 503 },
    )
  }

  const payload: HealthResponse = {
    status: "ok",
    documents: SAMPLE_STATS.documents,
    files: SAMPLE_STATS.files,
    folders: SAMPLE_STATS.folders,
    last_scan: SAMPLE_STATS.last_scan,
  }
  return NextResponse.json({ ...payload, sample: true, demo: true, authenticated: true })
}
