import { NextResponse } from "next/server"
import { backendBase, authHeaders } from "@/lib/backend"
import { requireAuth } from "@/lib/auth"

const ACTIONS = new Set(["corriger", "valider", "rejeter", "rouvrir"])

export const dynamic = "force-dynamic"

export async function POST(req: Request, { params }: { params: Promise<{ code: string; action: string }> }) {
  const denied = await requireAuth()
  if (denied) return denied
  const { code, action } = await params
  if (!ACTIONS.has(action)) return NextResponse.json({ detail: "Action inconnue." }, { status: 404 })
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  const payload = await req.json().catch(() => ({}))
  try {
    const res = await fetch(`${base}/fiches/${encodeURIComponent(code)}/${action}`, {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify(payload ?? {}),
      cache: "no-store",
    })
    return NextResponse.json(await res.json(), { status: res.status })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
