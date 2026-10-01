import { type NextRequest, NextResponse } from "next/server"
import { backendBase, authHeaders, requireAuthValide } from "@/lib/backend"
import { searchSample } from "@/lib/sample-data"
import type { SearchResponse } from "@/lib/types"

export const dynamic = "force-dynamic"

function masquerChemins(body: unknown) {
  if (!body || typeof body !== "object" || !Array.isArray((body as { results?: unknown }).results)) return body
  const payload = body as { results: Record<string, unknown>[]; [key: string]: unknown }
  return {
    ...payload,
    results: payload.results.map((result) => {
      const { path, path_key, object_key, parent, extraction_detail, ...sansChemin } = result
      const source = String(parent ?? "")
      const parentCourt = source.split(/[\\/]+/).filter(Boolean).pop() ?? ""
      return { ...sansChemin, parent: parentCourt }
    }),
  }
}

export async function GET(req: NextRequest) {
  const denied = await requireAuthValide()
  if (denied) return denied
  const { searchParams } = new URL(req.url)
  const q = (searchParams.get("q") ?? "").trim()
  const limit = Math.min(Math.max(Number(searchParams.get("limit") ?? 25), 1), 200)
  const offset = Math.max(Number(searchParams.get("offset") ?? 0), 0)

  if (!q) {
    return NextResponse.json({ detail: "Une requête est nécessaire." }, { status: 400 })
  }

  const base = backendBase()
  if (base) {
    try {
      const url = `${base}/search?q=${encodeURIComponent(q)}&limit=${limit}&offset=${offset}`
      const res = await fetch(url, { headers: authHeaders(), cache: "no-store" })
      const body = await res.json()
      return NextResponse.json(masquerChemins(body), { status: res.status })
    } catch {
      return NextResponse.json({ detail: "Le backend SEAMTECH est injoignable." }, { status: 502 })
    }
  }

  // Sample fallback — only allowed when explicitly in demo mode and never in production
  const demoMode = process.env.SEAMTECH_DEMO_MODE === "1"
  const isProd = process.env.NODE_ENV === "production"
  if (!demoMode || isProd) {
    return NextResponse.json(
      { detail: "Backend non configuré. Définissez SEAMTECH_API_URL ou activez SEAMTECH_DEMO_MODE=1 pour la démonstration." },
      { status: 503 },
    )
  }

  const { results, has_more } = searchSample(q, limit, offset)
  const payload: SearchResponse = { query: q, count: results.length, offset, limit, has_more, results }
  return NextResponse.json({ ...payload, demo: true })
}
