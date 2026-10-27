import { type NextRequest, NextResponse } from "next/server"
import { backendBase, backendHeaders, requireRolesServer } from "@/lib/backend"
import { ROLE_ADMIN, ROLE_APPROBATEUR, ROLE_UTILISATEUR } from "@/lib/roles"

export const dynamic = "force-dynamic"

const token_VALIDE = /^[A-Za-z0-9_-]{16,64}$/

export async function POST(_req: NextRequest, { params }: { params: Promise<{ jeton: string }> }) {
  const denied = await requireRolesServer(ROLE_ADMIN, ROLE_UTILISATEUR, ROLE_APPROBATEUR)
  if (denied) return denied
  const { jeton } = await params
  if (!token_VALIDE.test(jeton)) {
    return NextResponse.json({ detail: "Jeton de téléversement invalide." }, { status: 400 })
  }
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  try {
    const response = await fetch(
      `${base}/imports/dossier/televersements/${encodeURIComponent(jeton)}/terminer`,
      {
        method: "POST",
        headers: await backendHeaders(),
        cache: "no-store",
      },
    )
    return NextResponse.json(await response.json(), { status: response.status })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
