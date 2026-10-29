import { type NextRequest, NextResponse } from "next/server"
import { backendBase, backendHeaders, requireRolesServer } from "@/lib/backend"
import { ROLE_ADMIN, ROLE_APPROBATEUR, ROLE_UTILISATEUR } from "@/lib/roles"
import { relayerMultipart } from "@/lib/relais-multipart"

export const dynamic = "force-dynamic"

/**
 * Relais d'un fichier unique vers le backend, EN FLUX.
 *
 * Même contrat que le relais d'envoi de dossier (413 sur dépassement, abandon
 * du backend si le client disparaît, statut du backend recopié tel quel). La
 * concurrence est bornée par identité : deux onglets du même compte ne peuvent
 * pas saturer la mémoire du BFF avec des envois simultanés.
 */
export async function POST(req: NextRequest) {
  const denied = await requireRolesServer(ROLE_ADMIN, ROLE_UTILISATEUR, ROLE_APPROBATEUR)
  if (denied) return denied
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Import requires a configured backend." }, { status: 503 })

  const entetes = await backendHeaders()
  const resultat = await relayerMultipart(req, `${base}/imports/upload`, entetes, {
    cleConcurrence: `upload:${entetes["X-SEAMTECH-UTILISATEUR"] ?? "inconnu"}`,
  })
  if (resultat.type === "abandon") return new Response(null, { status: 499 })
  if (resultat.type === "refus") {
    return NextResponse.json({ detail: resultat.detail }, { status: resultat.statut })
  }
  return resultat.reponse as unknown as NextResponse
}
