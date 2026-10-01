import { type NextRequest, NextResponse } from "next/server"
import { backendBase, backendHeaders, requireAuthValide } from "@/lib/backend"

export const dynamic = "force-dynamic"

export async function GET(request: NextRequest) {
  const denied = await requireAuthValide()
  if (denied) return denied
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  try {
    const query = request.nextUrl.searchParams.toString()
    const response = await fetch(`${base}/pieces${query ? `?${query}` : ""}`, {
      headers: await backendHeaders(),
      cache: "no-store",
    })
    const payload = await response.json().catch(() => ({}))
    return NextResponse.json(payload, { status: response.status, headers: { "Cache-Control": "no-store" } })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
