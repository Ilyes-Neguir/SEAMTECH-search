import { NextResponse } from "next/server"
import { backendBase, backendHeaders, requireNominativeRoles } from "@/lib/backend"
import { ROLE_ADMIN, ROLE_APPROBATEUR, ROLE_LECTURE, ROLE_UTILISATEUR } from "@/lib/roles"

export const dynamic = "force-dynamic"

export async function POST() {
  const denied = await requireNominativeRoles(ROLE_ADMIN, ROLE_LECTURE, ROLE_UTILISATEUR, ROLE_APPROBATEUR)
  if (denied) return denied
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  try {
    const res = await fetch(`${base}/notifications/tout-lire`, {
      method: "POST",
      headers: await backendHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({}),
      cache: "no-store",
    })
    return NextResponse.json(await res.json().catch(() => ({ detail: `Réponse backend illisible (HTTP ${res.status}).` })), {
      status: res.status,
    })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
