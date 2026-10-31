import { NextResponse } from "next/server"
import { backendBase, backendHeaders, requireRolesServer } from "@/lib/backend"
import { ROLE_ADMIN, ROLE_APPROBATEUR, ROLE_UTILISATEUR } from "@/lib/roles"

export const dynamic = "force-dynamic"

export async function GET() {
  const denied = await requireRolesServer(ROLE_ADMIN, ROLE_UTILISATEUR, ROLE_APPROBATEUR)
  if (denied) return denied
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré." }, { status: 503 })
  try {
    const response = await fetch(`${base}/imports/dossier/televersements/capacites`, {
      headers: await backendHeaders(), cache: "no-store",
    })
    const capacites = await response.json().catch(() => ({}))
    return NextResponse.json(capacites, { status: response.status, headers: { "Cache-Control": "no-store" } })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
