import { NextResponse } from "next/server"
import { backendBase, backendHeaders, requireAuthValide } from "@/lib/backend"

export const dynamic = "force-dynamic"

export async function GET(req: Request) {
  const denied = await requireAuthValide()
  if (denied) return denied
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  const url = new URL(req.url)
  const a = url.searchParams.get("a")
  const b = url.searchParams.get("b")
  if (!a || !b) return NextResponse.json({ detail: "a et b requis" }, { status: 422 })
  try {
    const res = await fetch(`${base}/api/compare/sails?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`, {
      headers: await backendHeaders(),
      cache: "no-store",
    })
    return NextResponse.json(await res.json(), { status: res.status, headers: { "Cache-Control": "no-store" } })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
