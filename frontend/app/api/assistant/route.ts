import { type NextRequest, NextResponse } from "next/server"
import { backendBase, authHeaders } from "@/lib/backend"
import { requireAuth } from "@/lib/auth"

// Proxy Lot I : assistant sourcé (POST /assistant côté FastAPI). Miroir du
// patron app/api/recherche/route.ts — l'assistant est EXTRACTIF (réponses
// construites depuis la base, citations obligatoires, refus sans invention) :
// côté client, aucune donnée ne quitte l'infrastructure locale (RG14).
// En cas d'indisponibilité (503), le corps { etat: "indisponible", … } du
// backend est transmis tel quel pour que le panneau affiche l'état clair.

export const dynamic = "force-dynamic"

export async function POST(req: NextRequest) {
  const denied = await requireAuth()
  if (denied) return denied
  const base = backendBase()
  if (!base) {
    return NextResponse.json(
      { etat: "indisponible", detail: "Backend non configuré (SEAMTECH_API_URL).", citations: [] },
      { status: 503 },
    )
  }
  let corps: unknown
  try {
    corps = await req.json()
  } catch {
    return NextResponse.json({ etat: "indisponible", detail: "Corps JSON invalide.", citations: [] }, { status: 400 })
  }
  try {
    const res = await fetch(`${base}/assistant`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(corps),
      cache: "no-store",
    })
    return NextResponse.json(await res.json(), { status: res.status })
  } catch {
    return NextResponse.json(
      { etat: "indisponible", detail: "Backend SEAMTECH injoignable.", citations: [] },
      { status: 502 },
    )
  }
}
