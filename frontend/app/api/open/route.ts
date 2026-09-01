import { type NextRequest, NextResponse } from "next/server"
import { backendBase, authHeaders } from "@/lib/backend"

export async function POST(req: NextRequest) {
  const { searchParams } = new URL(req.url)
  const path = searchParams.get("path") ?? ""
  if (!path) return NextResponse.json({ detail: "Path is required." }, { status: 400 })

  const base = backendBase()
  if (base) {
    try {
      const res = await fetch(`${base}/open?path=${encodeURIComponent(path)}`, {
        method: "POST",
        headers: authHeaders(),
        cache: "no-store",
      })
      return NextResponse.json(await res.json(), { status: res.status })
    } catch {
      return NextResponse.json({ detail: "Could not reach the SEAMTECH backend." }, { status: 502 })
    }
  }

  // Sample fallback: opening a file on the host only works against the real
  // Windows-hosted backend, so make the boundary explicit here.
  return NextResponse.json(
    { detail: "Open File/Folder requires the SEAMTECH backend running on the Windows host." },
    { status: 501 },
  )
}
