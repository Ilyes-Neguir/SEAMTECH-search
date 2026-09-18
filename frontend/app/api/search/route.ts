import { type NextRequest, NextResponse } from "next/server"
import { backendBase, authHeaders } from "@/lib/backend"
import { searchSample } from "@/lib/sample-data"
import type { SearchResponse } from "@/lib/types"
import { requireAuth } from "@/lib/auth"

export const dynamic = "force-dynamic"

export async function GET(req: NextRequest) {
  const denied = await requireAuth()
  if (denied) return denied
  const { searchParams } = new URL(req.url)
  const q = (searchParams.get("q") ?? "").trim()
  const limit = Math.min(Math.max(Number(searchParams.get("limit") ?? 25), 1), 200)
  const offset = Math.max(Number(searchParams.get("offset") ?? 0), 0)

  if (!q) {
    return NextResponse.json({ detail: "Query is required." }, { status: 400 })
  }

  const base = backendBase()
  if (base) {
    try {
      const url = `${base}/search?q=${encodeURIComponent(q)}&limit=${limit}&offset=${offset}`
      const res = await fetch(url, { headers: authHeaders(), cache: "no-store" })
      const body = await res.json()
      return NextResponse.json(body, { status: res.status })
    } catch {
      return NextResponse.json({ detail: "Could not reach the SEAMTECH backend." }, { status: 502 })
    }
  }

  // Sample fallback — only allowed when explicitly in demo mode and never in production
  const demoMode = process.env.SEAMTECH_DEMO_MODE === "1"
  const isProd = process.env.NODE_ENV === "production"
  if (!demoMode || isProd) {
    return NextResponse.json(
      { detail: "Backend not configured. Set SEAMTECH_API_URL or enable SEAMTECH_DEMO_MODE=1 for demo." },
      { status: 503 },
    )
  }

  const { results, has_more } = searchSample(q, limit, offset)
  const payload: SearchResponse = { query: q, count: results.length, offset, limit, has_more, results }
  return NextResponse.json({ ...payload, demo: true })
}
