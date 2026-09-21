import { type NextRequest, NextResponse } from "next/server"
import { backendBase, authHeaders } from "@/lib/backend"
import { requireAuth } from "@/lib/auth"

// Proxy Lot E : suggestions de recherche (valeurs réellement présentes dans
// le corpus — référentiels + textes, jamais de lexique inventé).

export const dynamic = "force-dynamic"

export async function GET(req: NextRequest) {
  const denied = await requireAuth()
  if (denied) return denied
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  const query = req.nextUrl.searchParams.toString()
  try {
    const res = await fetch(`${base}/recherche/suggestions${query ? `?${query}` : ""}`, {
      headers: authHeaders(),
      cache: "no-store",
    })
    return NextResponse.json(await res.json(), { status: res.status })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
