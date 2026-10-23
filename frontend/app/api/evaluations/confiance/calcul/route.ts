import { NextResponse } from "next/server"
import { backendBase, backendHeaders, requireAuthValide } from "@/lib/backend"

export const dynamic = "force-dynamic"

export async function POST(req: Request) {
  const denied = await requireAuthValide()
  if (denied) return denied
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  const payload = await req.text()
  try {
    const res = await fetch(`${base}/api/evaluations/confiance/calcul`, {
      method: "POST",
      headers: await backendHeaders({ "Content-Type": "application/json" }),
      body: payload,
      cache: "no-store",
    })
    return NextResponse.json(await res.json(), { status: res.status, headers: { "Cache-Control": "no-store" } })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
