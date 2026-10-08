import { NextResponse } from "next/server"
import { backendBase, backendHeaders, requireAuthValide } from "@/lib/backend"

// ÉTAT COHÉRENT d'une fiche : statut + révision + champs en UNE réponse.
//
// L'écran de validation ouvrait la fiche par DEUX requêtes (champs, puis
// révision). Un collègue qui enregistrait entre les deux laissait le poste
// avec les anciennes valeurs affichées et la NOUVELLE révision : la correction
// suivante passait le verrou alors qu'elle portait sur un état jamais lu
// (revue indépendante du 2026-10-07, constat n°2). Cette route relaie
// l'instantané unique calculé côté backend.
export const dynamic = "force-dynamic"

export async function GET(_req: Request, { params }: { params: Promise<{ code: string }> }) {
  const denied = await requireAuthValide()
  if (denied) return denied
  const { code } = await params
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  try {
    const res = await fetch(`${base}/fiches/${encodeURIComponent(code)}/etat`, {
      headers: await backendHeaders(),
      cache: "no-store",
    })
    return NextResponse.json(await res.json(), { status: res.status })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
