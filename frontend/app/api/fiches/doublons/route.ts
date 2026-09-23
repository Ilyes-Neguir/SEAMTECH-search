import { type NextRequest, NextResponse } from "next/server"
import { backendBase, authHeaders } from "@/lib/backend"
import { requireAuth } from "@/lib/auth"

// Proxy Lot L.1 : détection de doublons (GET /fiches/doublons côté FastAPI).
//
// GET  → groupes EXACTS (même empreinte SHA-256) + paires PROBABLES (titres).
// POST → relance un scan ; `appliquer` vaut false par défaut côté Python, donc
//        un appel de consultation n'écrit jamais rien.
//
// PROPOSITIVE : rien n'est supprimé, rien n'est fusionné, aucun statut n'est
// modifié. Seules des lignes de lien peuvent être ajoutées, et uniquement sur
// un POST explicitement marqué `appliquer: true`.

export const dynamic = "force-dynamic"

export async function GET(req: NextRequest) {
  const denied = await requireAuth()
  if (denied) return denied
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  const query = req.nextUrl.searchParams.toString()
  try {
    const res = await fetch(`${base}/fiches/doublons${query ? `?${query}` : ""}`, {
      headers: authHeaders(),
      cache: "no-store",
    })
    return NextResponse.json(await res.json(), { status: res.status })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}

export async function POST(req: NextRequest) {
  const denied = await requireAuth()
  if (denied) return denied
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  const corps = await req.json().catch(() => ({}))
  try {
    const res = await fetch(`${base}/fiches/doublons/scan`, {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({
        seuil: corps?.seuil ?? 0.55,
        // Jamais true par défaut : écrire des liens doit rester un geste explicite.
        appliquer: corps?.appliquer === true,
      }),
      cache: "no-store",
    })
    return NextResponse.json(await res.json(), { status: res.status })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
