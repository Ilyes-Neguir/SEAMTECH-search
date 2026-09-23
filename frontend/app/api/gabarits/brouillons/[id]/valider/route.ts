import { type NextRequest, NextResponse } from "next/server"
import { backendBase, authHeaders, requireAuthValide } from "@/lib/backend"

export const dynamic = "force-dynamic"

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const denied = await requireAuthValide()
  if (denied) return denied
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré" }, { status: 503 })
  const { id } = await params
  try {
    const res = await fetch(`${base}/gabarits/brouillons/${id}/valider`, {
      method: "POST",
      headers: authHeaders(),
    })
    const json = await res.json()
    return NextResponse.json(json, { status: res.status })
  } catch (e) {
    return NextResponse.json({ detail: `Backend injoignable: ${e}` }, { status: 502 })
  }
}
