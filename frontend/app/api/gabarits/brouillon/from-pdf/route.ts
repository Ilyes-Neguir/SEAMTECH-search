import { type NextRequest, NextResponse } from "next/server"
import { backendBase, backendHeaders, requireRolesServer } from "@/lib/backend"
import { relayerMultipart } from "@/lib/relais-multipart"
import { ROLE_ADMIN } from "@/lib/roles"

export const dynamic = "force-dynamic"

// 20 Mio de PDF côté backend (TAILLE_PDF_MAX) + marge pour l'encadrement
// multipart. La borne est volontairement plus serrée que la limite
// d'administration (64 Mio) : un envoi démesuré est refusé au BFF (413) au
// lieu d'être relayé pour rien vers le backend.
const LIMITE_GABARIT_OCTETS = 21 * 1024 * 1024

/**
 * Relais EN FLUX du PDF de gabarit : corps transmis tel quel, borné (413),
 * annulé si le navigateur abandonne (499), statut du backend recopié. La
 * concurrence est bornée par identité : deux onglets du même compte ne
 * saturent pas la mémoire du BFF.
 */
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
