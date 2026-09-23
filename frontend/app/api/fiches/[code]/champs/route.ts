import { NextResponse } from "next/server"
import { backendBase, authHeaders, requireAuthValide } from "@/lib/backend"

export const dynamic = "force-dynamic"

export async function GET(_req: Request, { params }: { params: Promise<{ code: string }> }) {
  const denied = await requireAuthValide()
  if (denied) return denied
  const { code } = await params
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  try {
    const res = await fetch(`${base}/fiches/${encodeURIComponent(code)}/champs`, { headers: authHeaders(), cache: "no-store" })
    return NextResponse.json(await res.json(), { status: res.status })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
