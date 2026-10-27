import { type NextRequest, NextResponse } from "next/server"
import { proxyMultipartBorne, requireRolesServer } from "@/lib/backend"
import { ROLE_ADMIN, ROLE_APPROBATEUR, ROLE_UTILISATEUR } from "@/lib/roles"

export const dynamic = "force-dynamic"

const token_VALIDE = /^[A-Za-z0-9_-]{16,64}$/
// 512 Mio de fichiers au maximum côté backend + marge pour les frontières
// multipart et le JSON des chemins.
const MAX_ENVOI_MULTIPART = 520 * 1024 * 1024

export async function POST(req: NextRequest, { params }: { params: Promise<{ jeton: string }> }) {
  const denied = await requireRolesServer(ROLE_ADMIN, ROLE_UTILISATEUR, ROLE_APPROBATEUR)
  if (denied) return denied
  const { jeton } = await params
  if (!token_VALIDE.test(jeton)) {
    return NextResponse.json({ detail: "Jeton de téléversement invalide." }, { status: 400 })
  }
  return proxyMultipartBorne(
    req,
    `/imports/dossier/televersements/${encodeURIComponent(jeton)}/fichiers`,
    MAX_ENVOI_MULTIPART,
  )
}
