import { type NextRequest, NextResponse } from "next/server"
import { backendBase, authHeaders, requireAuthValide } from "@/lib/backend"

// Proxy Lot J : journal de recherche (GET /recherche/journal côté FastAPI).
// Agrège la table réelle recherche_log : top requêtes + sans résultat.

export const dynamic = "force-dynamic"

export async function GET(req: NextRequest) {
  const denied = await requireAuthValide()
  if (denied) return denied
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  const query = req.nextUrl.searchParams.toString()
  try {
    const res = await fetch(`${base}/recherche/journal${query ? `?${query}` : ""}`, {
      headers: authHeaders(),
      cache: "no-store",
    })
    return NextResponse.json(await res.json(), { status: res.status })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
