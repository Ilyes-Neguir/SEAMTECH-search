import { type NextRequest, NextResponse } from "next/server"
import { backendBase, backendHeaders, requireRolesServer } from "@/lib/backend"
import { ROLE_ADMIN, ROLE_APPROBATEUR, ROLE_UTILISATEUR } from "@/lib/roles"

/**
 * Passage EXPLICITE d'un rapport d'analyse à une FICHE.
 *
 * « Importer les fichiers sélectionnés » produit un rapport (imports +
 * import_jobs) : il n'écrit ni fiche ni dossier, donc rien n'apparaît dans
 * « Dossiers ». Cette route relaie le dépôt du dossier qui contient le PDF
 * technique identifié — mêmes règles, même transaction, même idempotence
 * qu'un dépôt de dossier classique.
 */
export const dynamic = "force-dynamic"

export async function POST(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const denied = await requireRolesServer(ROLE_ADMIN, ROLE_UTILISATEUR, ROLE_APPROBATEUR)
  if (denied) return denied
  const { id } = await params
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Import requires a configured backend." }, { status: 503 })
  try {
    const response = await fetch(`${base}/imports/${encodeURIComponent(id)}/creer-fiche`, {
      method: "POST",
      headers: await backendHeaders(),
      cache: "no-store",
    })
    return NextResponse.json(await response.json(), { status: response.status })
  } catch {
    return NextResponse.json({ detail: "Could not reach the SEAMTECH backend." }, { status: 502 })
  }
}
