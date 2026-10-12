// Relais de suivi de lot — DÉFAUT A08 de l'audit du 2026-10-08.
//
// L'écran « Nouveau dossier » (components/nouveau-app.tsx) appelle
// `/api/lots/{id}` après un dépôt pour afficher la progression du lot. Cette
// route n'existait pas : le proxy répondait 404, et l'écran — qui avalait
// l'erreur — n'affichait AUCUN suivi. L'opérateur voyait « Dossier traité »
// puis plus rien, sans savoir si le lot était en cours, terminé ou en échec.
//
// Même contrat que les autres relais : session nominative vérifiée AUPRÈS DU
// BACKEND (requireAuthValide), jeton de service ajouté côté serveur, aucune
// donnée mise en cache, et une erreur du backend est RELAYÉE telle quelle —
// jamais transformée en 404 silencieux.

import { type NextRequest, NextResponse } from "next/server"
import { backendBase, authHeaders, requireAuthValide } from "@/lib/backend"

export const dynamic = "force-dynamic"

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const denied = await requireAuthValide()
  if (denied) return denied

  const { id } = await params
  // L'identifiant est validé AVANT l'appel : un segment non numérique ne doit
  // pas devenir une requête backend arbitraire (le backend répondrait 422,
  // mais autant le dire ici, avec un message utile à l'opérateur).
  if (!/^\d+$/.test(id)) {
    return NextResponse.json({ detail: `Identifiant de lot invalide : « ${id} » (numéro attendu).` }, { status: 400 })
  }

  const base = backendBase()
  if (!base) {
    return NextResponse.json({ detail: "Le suivi de lot exige un backend configuré (SEAMTECH_API_URL)." }, { status: 503 })
  }

  try {
    const response = await fetch(`${base}/lots/${encodeURIComponent(id)}`, {
      headers: authHeaders(),
      cache: "no-store",
    })
    const corps = await response.json().catch(() => ({ detail: `Réponse backend illisible (HTTP ${response.status}).` }))
    return NextResponse.json(corps, { status: response.status })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable : suivi de lot indisponible." }, { status: 502 })
  }
}
