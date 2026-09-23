import { type NextRequest, NextResponse } from "next/server"
import { backendBase, authHeaders } from "@/lib/backend"
import { requireAuth } from "@/lib/auth"

// Proxy Lot L.1 : liens de doublon pour la file de /validation.
//
// GET  /api/validation/doublons?codes=A,B,C  → { par_code: { A: [...], ... } }
// POST /api/validation/doublons { codes: [] } → même chose (liste longue)
//
// LECTURE SEULE côté écran : le bandeau « Doublon exact/probable de CODE »
// prévient AVANT la validation, il ne fusionne jamais rien. Aucun bouton
// « fusionner » n'est exposé — la décision reste humaine.

export const dynamic = "force-dynamic"

async function relayer(codes: string[]) {
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  if (codes.length === 0) return NextResponse.json({ par_code: {} })
  try {
    const res = await fetch(`${base}/validation/doublons`, {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ codes }),
      cache: "no-store",
    })
    return NextResponse.json(await res.json(), { status: res.status })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}

export async function GET(req: NextRequest) {
  const denied = await requireAuth()
  if (denied) return denied
  const brut = req.nextUrl.searchParams.get("codes") ?? ""
  const codes = brut
    .split(",")
    .map((c) => c.trim())
    .filter(Boolean)
  return relayer(codes)
}

export async function POST(req: NextRequest) {
  const denied = await requireAuth()
  if (denied) return denied
  const corps = await req.json().catch(() => ({}))
  const codes = Array.isArray(corps?.codes) ? corps.codes.map((c: unknown) => String(c)) : []
  return relayer(codes)
}
