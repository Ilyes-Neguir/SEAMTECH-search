import { NextResponse } from "next/server"
import { requireAuthValide } from "@/lib/backend"

export const dynamic = "force-dynamic"

/**
 * L’ancienne prévisualisation par chemin est désactivée côté navigateur.
 * L’aperçu passe désormais par /api/pieces/{id}/apercu et ne révèle aucun
 * emplacement local ou chemin d’objet.
 */
export async function GET() {
  const denied = await requireAuthValide()
  if (denied) return denied
  return NextResponse.json(
    { detail: "Cette route a été remplacée par l’aperçu des fichiers par identifiant." },
    { status: 410, headers: { "Cache-Control": "no-store" } },
  )
}
