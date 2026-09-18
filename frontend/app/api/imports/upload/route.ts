import { type NextRequest, NextResponse } from "next/server"
import { backendBase, authHeaders } from "@/lib/backend"
import { requireAuth } from "@/lib/auth"

export const dynamic = "force-dynamic"

export async function POST(req: NextRequest) {
  const denied = await requireAuth()
  if (denied) return denied
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Import requires a configured backend." }, { status: 503 })
  try {
    const form = await req.formData()
    const response = await fetch(`${base}/imports/upload`, {
      method: "POST",
      headers: { ...authHeaders() },
      body: form,
      cache: "no-store",
    })
    return NextResponse.json(await response.json(), { status: response.status })
  } catch {
    return NextResponse.json({ detail: "Could not reach the SEAMTECH backend." }, { status: 502 })
  }
}
