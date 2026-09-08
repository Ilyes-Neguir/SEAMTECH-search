import { type NextRequest, NextResponse } from "next/server"
import { backendBase, authHeaders } from "@/lib/backend"

export async function POST(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Import requires a configured backend." }, { status: 503 })
  try {
    const response = await fetch(`${base}/imports/${encodeURIComponent(id)}/retry-upload`, {
      method: "POST",
      headers: { ...authHeaders() },
      cache: "no-store",
    })
    return NextResponse.json(await response.json(), { status: response.status })
  } catch {
    return NextResponse.json({ detail: "Could not reach the SEAMTECH backend." }, { status: 502 })
  }
}
