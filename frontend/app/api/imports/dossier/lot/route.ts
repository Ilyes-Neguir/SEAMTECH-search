import { NextResponse } from "next/server"
import { backendBase, backendHeaders, requireRolesServer } from "@/lib/backend"
import { ROLE_ADMIN, ROLE_APPROBATEUR, ROLE_UTILISATEUR } from "@/lib/roles"

export const dynamic = "force-dynamic"

export async function POST(req: Request) {
  const denied = await requireRolesServer(ROLE_ADMIN, ROLE_UTILISATEUR, ROLE_APPROBATEUR)
  if (denied) return denied
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  const payload = await req.json().catch(() => null)
  const racine = typeof payload?.racine === "string" ? payload.racine.trim() : ""
  const jetonTeleversement =
    typeof payload?.token_televersement === "string" ? payload.token_televersement.trim() : ""
  if (Boolean(racine) === Boolean(jetonTeleversement)) {
    return NextResponse.json(
      { detail: "Précisez exactement une source : racine serveur ou token_televersement." },
      { status: 400 },
    )
  }
  if (jetonTeleversement && !/^[A-Za-z0-9_-]{16,64}$/.test(jetonTeleversement)) {
    return NextResponse.json({ detail: "Jeton de téléversement invalide." }, { status: 400 })
  }
  try {
    const res = await fetch(`${base}/imports/dossier/lot`, {
      method: "POST",
      headers: await backendHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        ...(racine ? { racine } : {}),
        ...(jetonTeleversement ? { token_televersement: jetonTeleversement } : {}),
        notes: typeof payload?.notes === "string" ? payload.notes : undefined,
        recursif: typeof payload?.recursif === "boolean" ? payload.recursif : true,
      }),
      cache: "no-store",
    })
    return NextResponse.json(await res.json(), { status: res.status })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
