import { type NextRequest, NextResponse } from "next/server"
import { backendBase, backendHeaders, requireAuthValide, requireNominativeRoles } from "@/lib/backend"
import { ROLE_APPROBATEUR } from "@/lib/roles"

export const dynamic = "force-dynamic"

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  // Borner aussi la vérification de session nominative, pas seulement la
  // lecture métier. Une déconnexion du navigateur interrompt les deux appels.
  const expiration = AbortSignal.timeout(7_000)
  const signal = AbortSignal.any([req.signal, expiration])
  try {
    const denied = await requireAuthValide(signal)
    if (denied) return denied
    const { id } = await params
    const base = backendBase()
    if (!base) return NextResponse.json({ detail: "Import requires a configured backend." }, { status: 503 })
    const response = await fetch(`${base}/imports/${encodeURIComponent(id)}`, {
      headers: await backendHeaders(),
      cache: "no-store",
      signal,
    })
    return NextResponse.json(await response.json(), { status: response.status, headers: { "Cache-Control": "no-store" } })
  } catch {
    return NextResponse.json(
      { detail: expiration.aborted ? "Délai de suivi dépassé." : "Could not reach the SEAMTECH backend." },
      { status: expiration.aborted ? 504 : 502, headers: { "Cache-Control": "no-store" } },
    )
  }
}

export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const denied = await requireNominativeRoles(ROLE_APPROBATEUR)
  if (denied) return denied
  const { id } = await params
  const payload = await req.json().catch(() => null)
  if (!payload || typeof payload !== "object") {
    return NextResponse.json({ detail: "Correction payload is required." }, { status: 400 })
  }
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Import requires a configured backend." }, { status: 503 })
  try {
    const response = await fetch(`${base}/imports/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: await backendHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
      cache: "no-store",
    })
    return NextResponse.json(await response.json(), { status: response.status })
  } catch {
    return NextResponse.json({ detail: "Could not reach the SEAMTECH backend." }, { status: 502 })
  }
}
