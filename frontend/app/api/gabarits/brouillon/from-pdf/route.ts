import { type NextRequest, NextResponse } from "next/server"
import { backendBase, backendHeaders, requireRolesServer } from "@/lib/backend"
import { ROLE_ADMIN } from "@/lib/roles"
import { relayerMultipart } from "@/lib/relais-multipart"

export const dynamic = "force-dynamic"

/** Un PDF de gabarit est petit : borne serrée pour ne pas relayer un envoi
 *  démesuré sur cette route d'administration (le backend refuserait aussi,
 *  mais autant couper côté BFF). */
const LIMITE_GABARIT_OCTETS = 64 * 1024 * 1024

export async function POST(req: NextRequest) {
  const denied = await requireRolesServer(ROLE_ADMIN)
  if (denied) return denied
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré" }, { status: 503 })

  const entetes = await backendHeaders()
  const resultat = await relayerMultipart(req, `${base}/gabarits/brouillon/from-pdf`, entetes, {
    limiteOctets: LIMITE_GABARIT_OCTETS,
    cleConcurrence: `gabarit:${entetes["X-SEAMTECH-UTILISATEUR"] ?? "inconnu"}`,
  })
  if (resultat.type === "abandon") return new Response(null, { status: 499 })
  if (resultat.type === "refus") {
    return NextResponse.json({ detail: resultat.detail }, { status: resultat.statut })
  }
  return resultat.reponse as unknown as NextResponse
}
