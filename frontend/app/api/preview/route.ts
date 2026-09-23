import { type NextRequest, NextResponse } from "next/server"
import { backendBase, authHeaders, requireAuthValide } from "@/lib/backend"
import { SAMPLE_FILES } from "@/lib/sample-data"
import type { PreviewResponse } from "@/lib/types"

export const dynamic = "force-dynamic"

export async function GET(req: NextRequest) {
  const denied = await requireAuthValide()
  if (denied) return denied
  const { searchParams } = new URL(req.url)
  const path = searchParams.get("path") ?? ""
  if (!path) return NextResponse.json({ detail: "Path is required." }, { status: 400 })

  const base = backendBase()
  if (base) {
    try {
      const res = await fetch(`${base}/preview?path=${encodeURIComponent(path)}`, {
        headers: authHeaders(),
        cache: "no-store",
      })
      return NextResponse.json(await res.json(), { status: res.status })
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

  const file = SAMPLE_FILES.find((f) => f.path === path)
  if (!file) return NextResponse.json({ detail: "Path does not exist." }, { status: 404 })

  if (file.is_dir) {
    const children = SAMPLE_FILES.filter(
      (f) => f.path.startsWith(file.path + "\\") && !f.path.slice(file.path.length + 1).includes("\\"),
    )
    const payload: PreviewResponse = {
      path: file.path,
      name: file.name,
      is_dir: true,
      children: children.map((c) => ({ name: c.name, path: c.path, is_dir: c.is_dir, size: c.size })),
    }
    return NextResponse.json({ ...(payload as any), demo: true })
  }

  const payload: PreviewResponse = {
    path: file.path,
    name: file.name,
    is_dir: false,
    extension: file.name.includes(".") ? file.name.slice(file.name.lastIndexOf(".")).toLowerCase() : "",
    size: file.size,
    text: file.content ?? "Preview is not available for this file type. Use Open File instead.",
  }
  return NextResponse.json({ ...(payload as any), demo: true })
}
