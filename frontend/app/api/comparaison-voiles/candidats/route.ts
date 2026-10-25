import { type NextRequest, NextResponse } from "next/server"
import { backendBase, backendHeaders, requireNominativeRoles } from "@/lib/backend"
import { ROLE_ADMIN, ROLE_APPROBATEUR, ROLE_LECTURE, ROLE_UTILISATEUR } from "@/lib/roles"

export const dynamic = "force-dynamic"

const ROLES_NOMINATIFS = [ROLE_ADMIN, ROLE_LECTURE, ROLE_UTILISATEUR, ROLE_APPROBATEUR] as const

export async function GET(req: NextRequest) {
  const denied = await requireNominativeRoles(...ROLES_NOMINATIFS)
  if (denied) return denied
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  const query = req.nextUrl.searchParams.toString()
  try {
    const res = await fetch(`${base}/comparaison-voiles/candidats${query ? `?${query}` : ""}`, {
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
