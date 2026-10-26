import { type NextRequest, NextResponse } from "next/server"
import { backendBase, backendHeaders, requireAuthValide } from "@/lib/backend"

// Proxy Lot 2 : route canonique des suggestions de fiches. L'ancienne route
// /api/recherche/suggestions reste disponible pour compatibilité ; les deux
// exposent le même classement exact > alias > préfixe > flou du backend.

export const dynamic = "force-dynamic"

export async function GET(req: NextRequest) {
  const denied = await requireAuthValide()
  if (denied) return denied
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  const query = req.nextUrl.searchParams.toString()
  try {
    const res = await fetch(`${base}/search/suggestions${query ? `?${query}` : ""}`, {
      headers: await backendHeaders(),
      cache: "no-store",
    })
    return NextResponse.json(await res.json(), { status: res.status })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
