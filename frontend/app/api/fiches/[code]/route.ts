import { NextResponse } from "next/server"
import { backendBase, backendHeaders, requireAuthValide } from "@/lib/backend"

export const dynamic = "force-dynamic"

export async function GET(_request: Request, { params }: { params: Promise<{ code: string }> }) {
  const denied = await requireAuthValide()
  if (denied) return denied
  const { code } = await params
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  try {
    const response = await fetch(`${base}/fiches/${encodeURIComponent(code)}`, {
      headers: await backendHeaders(),
      cache: "no-store",
    })
    return NextResponse.json(await response.json(), { status: response.status })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
