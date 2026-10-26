import { type NextRequest, NextResponse } from "next/server"
import { backendBase, backendHeaders, requireNominativeRoles } from "@/lib/backend"
import { ROLE_ADMIN, ROLE_APPROBATEUR, ROLE_LECTURE, ROLE_UTILISATEUR } from "@/lib/roles"

export const dynamic = "force-dynamic"

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const denied = await requireNominativeRoles(ROLE_ADMIN, ROLE_LECTURE, ROLE_UTILISATEUR, ROLE_APPROBATEUR)
  if (denied) return denied
  const { id } = await params
  if (!/^\d+$/.test(id)) {
    return NextResponse.json({ detail: `Identifiant de demande invalide : « ${id} » (numéro attendu).` }, { status: 400 })
  }
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  const corps = await req.json().catch(() => ({}))
  try {
    const res = await fetch(`${base}/approvals/${encodeURIComponent(id)}/annuler`, {
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
