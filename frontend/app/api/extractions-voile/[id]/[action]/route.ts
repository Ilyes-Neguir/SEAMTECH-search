import { type NextRequest, NextResponse } from "next/server"
import { backendBase, backendHeaders, requireNominativeRoles } from "@/lib/backend"
import { ROLE_APPROBATEUR } from "@/lib/roles"

export const dynamic = "force-dynamic"

const ACTIONS = new Set(["resoudre", "lier", "corriger"])

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string; action: string }> }) {
  const denied = await requireNominativeRoles(ROLE_APPROBATEUR)
  if (denied) return denied
  const { id, action } = await params
  if (!/^\d+$/.test(id)) {
    return NextResponse.json({ detail: `Identifiant d’extraction invalide : « ${id} » (numéro attendu).` }, { status: 400 })
  }
  if (!ACTIONS.has(action)) {
    return NextResponse.json({ detail: `Action d’extraction inconnue : « ${action} ».` }, { status: 404 })
  }
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  const corps = await req.json().catch(() => ({}))
  try {
    const res = await fetch(`${base}/extractions-voile/${encodeURIComponent(id)}/${action}`, {
      method: "POST",
      headers: await backendHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(corps ?? {}),
      cache: "no-store",
    })
    return NextResponse.json(await res.json().catch(() => ({ detail: `Réponse backend illisible (HTTP ${res.status}).` })), {
      status: res.status,
    })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
