import { type NextRequest, NextResponse } from "next/server"
import { backendBase, backendHeaders, requireRolesServer } from "@/lib/backend"
import { ROLE_ADMIN, ROLE_APPROBATEUR, ROLE_UTILISATEUR } from "@/lib/roles"

export const dynamic = "force-dynamic"

export async function POST(req: NextRequest) {
  const denied = await requireRolesServer(ROLE_ADMIN, ROLE_UTILISATEUR, ROLE_APPROBATEUR)
  if (denied) return denied
  const payload = await req.json().catch(() => null)
  if (
    !payload ||
    typeof payload !== "object" ||
    typeof payload.nom_racine !== "string" ||
    !Number.isInteger(payload.nombre_fichiers) ||
    !Number.isInteger(payload.taille_totale_octets) ||
    (payload.cle_idempotence !== undefined &&
      (typeof payload.cle_idempotence !== "string" || !/^[0-9a-f]{64}$/.test(payload.cle_idempotence)))
  ) {
    return NextResponse.json(
      { detail: "Corps attendu : nom_racine, nombre_fichiers et taille_totale_octets." },
      { status: 400 },
    )
  }
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  try {
    // L'ancienne API ignorait les champs inconnus. Une URL distincte évite
    // qu'un backend ancien crée silencieusement une session non idempotente.
    const suffixe = payload.cle_idempotence !== undefined ? "/v2" : ""
    const response = await fetch(`${base}/imports/dossier/televersements${suffixe}`, {
      method: "POST",
      headers: await backendHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        nom_racine: payload.nom_racine,
        nombre_fichiers: payload.nombre_fichiers,
        taille_totale_octets: payload.taille_totale_octets,
        ...(payload.cle_idempotence !== undefined ? { cle_idempotence: payload.cle_idempotence } : {}),
      }),
      cache: "no-store",
    })
    return NextResponse.json(await response.json(), { status: response.status })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
