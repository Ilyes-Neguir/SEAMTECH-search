import { type NextRequest, NextResponse } from "next/server"
import { backendBase, backendHeaders, requireRolesServer } from "@/lib/backend"
import { ROLE_ADMIN, ROLE_APPROBATEUR, ROLE_UTILISATEUR } from "@/lib/roles"
import { relayerMultipart } from "@/lib/relais-multipart"

export const dynamic = "force-dynamic"

const token_VALIDE = /^[A-Za-z0-9_-]{16,64}$/

/**
 * Relais d'un envoi de dossier vers le backend, EN FLUX.
 *
 * Cette route ne lit plus `req.formData()` : le corps multipart est transmis
 * tel quel, borné (413), annulé si le navigateur abandonne, et le statut du
 * backend est recopié tel quel (voir `lib/relais-multipart.ts`). La validation
 * du contenu (chemins JSON + fichiers) reste celle du backend, qui répond 400
 * avec le détail — un multipart incomplet n'est plus transformé en 502.
 */
export async function POST(req: NextRequest, { params }: { params: Promise<{ jeton: string }> }) {
  const denied = await requireRolesServer(ROLE_ADMIN, ROLE_UTILISATEUR, ROLE_APPROBATEUR)
  if (denied) return denied
  const { jeton } = await params
  if (!token_VALIDE.test(jeton)) {
    return NextResponse.json({ detail: "Jeton de téléversement invalide." }, { status: 400 })
  }
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })

  const resultat = await relayerMultipart(
    req,
    `${base}/imports/dossier/televersements/${encodeURIComponent(jeton)}/fichiers`,
    await backendHeaders(),
    { cleConcurrence: jeton },
  )
  if (resultat.type === "abandon") return new Response(null, { status: 499 })
  if (resultat.type === "refus") {
    return NextResponse.json({ detail: resultat.detail }, { status: resultat.statut })
  }
  return resultat.reponse as unknown as NextResponse
}
