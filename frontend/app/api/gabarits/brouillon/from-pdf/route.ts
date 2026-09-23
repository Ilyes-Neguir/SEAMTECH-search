import { type NextRequest, NextResponse } from "next/server"
import { backendBase, authHeaders, requireAuthValide } from "@/lib/backend"

export const dynamic = "force-dynamic"

export async function POST(req: NextRequest) {
  const denied = await requireAuthValide()
  if (denied) return denied
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré" }, { status: 503 })
  try {
    const form = await req.formData()
    const res = await fetch(`${base}/gabarits/brouillon/from-pdf`, {
      method: "POST",
      headers: authHeaders(),
      body: form,
    })
    const json = await res.json()
    return NextResponse.json(json, { status: res.status })
  } catch (e) {
    return NextResponse.json({ detail: `Backend injoignable: ${e}` }, { status: 502 })
  }
}
