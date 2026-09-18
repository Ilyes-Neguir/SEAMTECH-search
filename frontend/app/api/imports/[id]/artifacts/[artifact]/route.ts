import { type NextRequest, NextResponse } from "next/server"
import { backendBase, authHeaders } from "@/lib/backend"
import { requireAuth } from "@/lib/auth"

export const dynamic = "force-dynamic"

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string; artifact: string }> }) {
  const denied = await requireAuth()
  if (denied) return denied
  const { id, artifact } = await params
  const allowed = ["report_pdf", "report_docx", "source_pdf", "source_excel"]
  if (!allowed.includes(artifact)) {
    return NextResponse.json({ detail: `Unknown artifact. Allowed: ${allowed.join(", ")}` }, { status: 400 })
  }

  const base = backendBase()
  if (!base) {
    return NextResponse.json({ detail: "Artifact download requires a configured backend." }, { status: 503 })
  }

  try {
    const url = `${base}/imports/${encodeURIComponent(id)}/artifacts/${encodeURIComponent(artifact)}`
    const res = await fetch(url, {
      headers: authHeaders(),
      cache: "no-store",
      redirect: "manual",
    })

    // If backend returns 302 to presigned URL, forward the redirect location as JSON for frontend to open
    if (res.status >= 300 && res.status < 400) {
      const location = res.headers.get("location")
      if (location) {
        // For browser download, we can redirect directly
        return NextResponse.redirect(location, { status: 302 })
      }
    }

    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: "Artifact not found" }))
      return NextResponse.json(body, { status: res.status })
    }

    // If backend streams file directly, proxy it
    const contentType = res.headers.get("content-type") || "application/octet-stream"
    const contentDisposition = res.headers.get("content-disposition") || `attachment; filename="${artifact}"`
    const buffer = await res.arrayBuffer()
    return new NextResponse(buffer, {
      status: 200,
      headers: {
        "Content-Type": contentType,
        "Content-Disposition": contentDisposition,
      },
    })
  } catch {
    return NextResponse.json({ detail: "Could not reach the SEAMTECH backend." }, { status: 502 })
  }
}
