import { type NextRequest, NextResponse } from "next/server"
import { backendBase, authHeaders } from "@/lib/backend"

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Import requires a configured backend." }, { status: 503 })
  try {
    const response = await fetch(`${base}/imports/${encodeURIComponent(id)}`, {
      headers: { ...authHeaders() },
      cache: "no-store",
    })
    return NextResponse.json(await response.json(), { status: response.status })
  } catch {
    return NextResponse.json({ detail: "Could not reach the SEAMTECH backend." }, { status: 502 })
  }
}

export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
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
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      cache: "no-store",
    })
    return NextResponse.json(await response.json(), { status: response.status })
  } catch {
    return NextResponse.json({ detail: "Could not reach the SEAMTECH backend." }, { status: 502 })
  }
}
