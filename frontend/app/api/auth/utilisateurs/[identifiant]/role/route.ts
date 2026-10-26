import { type NextRequest, NextResponse } from "next/server"
import { backendBase, backendHeaders, requireRolesServer } from "@/lib/backend"
import { normaliserRole, ROLE_ADMIN } from "@/lib/roles"

export const dynamic = "force-dynamic"

/** Audited role-change proxy. The backend revokes every target session. */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ identifiant: string }> },
) {
  const denied = await requireRolesServer(ROLE_ADMIN)
  if (denied) return denied
  const { identifiant } = await params
  const corps = (await req.json().catch(() => ({}))) as Record<string, unknown>
  const role = normaliserRole(corps.role)
  if (!role) {
    return NextResponse.json(
      { detail: "Rôle invalide : admin, lecture, utilisateur ou approbateur attendu." },
      { status: 422 },
    )
  }
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  try {
    const res = await fetch(`${base}/auth/utilisateurs/${encodeURIComponent(identifiant)}/role`, {
      method: "POST",
      headers: await backendHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ role }),
      cache: "no-store",
    })
    return NextResponse.json(await res.json().catch(() => ({ detail: `Réponse backend illisible (HTTP ${res.status}).` })), {
      status: res.status,
    })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
