import { type NextRequest, NextResponse } from "next/server"
import { backendBase, authHeaders, requireAuthValide } from "@/lib/backend"

export const dynamic = "force-dynamic"

export async function POST(req: NextRequest) {
  const denied = await requireAuthValide()
  if (denied) return denied
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
        redirect: "manual",
      })
      // If backend returns redirect to presigned URL, forward it
      if (res.status >= 300 && res.status < 400) {
        const location = res.headers.get("location")
        if (location) {
          return NextResponse.json({ url: location, redirect: true }, { status: 200 })
        }
      }
      const body = await res.json().catch(() => ({}))
      return NextResponse.json(body, { status: res.status })
    } catch {
      return NextResponse.json({ detail: "Could not reach the SEAMTECH backend." }, { status: 502 })
    }
  }

  const demoMode = process.env.SEAMTECH_DEMO_MODE === "1"
  const isProd = process.env.NODE_ENV === "production"
  if (!demoMode || isProd) {
    return NextResponse.json(
      { detail: "Backend not configured. Set SEAMTECH_API_URL or enable SEAMTECH_DEMO_MODE=1 for demo." },
      { status: 503 },
    )
  }

  return NextResponse.json(
    { detail: "Open File/Folder requires the SEAMTECH backend. In demo mode this is not available." },
    { status: 501 },
  )
}
