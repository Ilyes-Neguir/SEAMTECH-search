import { NextResponse } from "next/server"
import { requireAuthValide } from "@/lib/backend"

export const dynamic = "force-dynamic"

/**
 * L’ancienne ouverture par chemin est désactivée côté navigateur. Les écrans
 * utilisent désormais /api/pieces/{id}/apercu et /telecharger, qui ne relaient
 * ni chemin local ni URL présignée au client.
 */
export async function POST() {
  const denied = await requireAuthValide()
  if (denied) return denied
  return NextResponse.json(
    { detail: "Cette route a été remplacée par l’ouverture des fichiers par identifiant." },
    { status: 410, headers: { "Cache-Control": "no-store" } },
  )
}
