import { NextResponse } from "next/server"
import { backendBase, backendHeaders, requireNominativeRoles } from "@/lib/backend"
import { ROLE_ADMIN, ROLE_APPROBATEUR, ROLE_LECTURE, ROLE_UTILISATEUR } from "@/lib/roles"

export const dynamic = "force-dynamic"

const ROLES_NOMINATIFS = [ROLE_ADMIN, ROLE_LECTURE, ROLE_UTILISATEUR, ROLE_APPROBATEUR] as const

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const denied = await requireNominativeRoles(...ROLES_NOMINATIFS)
  if (denied) return denied
  const { id } = await params
  if (!/^\d+$/.test(id)) {
    return NextResponse.json({ detail: `Identifiant d’extraction invalide : « ${id} » (numéro attendu).` }, { status: 400 })
  }
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  try {
    const res = await fetch(`${base}/extractions-voile/${encodeURIComponent(id)}`, {
      headers: await backendHeaders(),
      cache: "no-store",
    })
    return NextResponse.json(await res.json().catch(() => ({ detail: `Réponse backend illisible (HTTP ${res.status}).` })), {
      status: res.status,
    })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
