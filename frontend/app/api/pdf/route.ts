import { type NextRequest, NextResponse } from "next/server"
import { backendBase, authHeaders, requireAuthValide } from "@/lib/backend"

export const dynamic = "force-dynamic"

// Flux binaire du PDF d'archive pour la visionneuse (pdf.js bundlé, hors ligne).
// Le backend sert le fichier via POST /open (FileResponse ; 302 présigné si S3).
// Le jeton serveur ne quitte JAMAIS le proxy : le navigateur ne le voit pas.
export async function GET(req: NextRequest) {
  const denied = await requireAuthValide()
  if (denied) return denied
  const path = req.nextUrl.searchParams.get("path") ?? ""
  if (!path) return NextResponse.json({ detail: "Paramètre « path » requis." }, { status: 400 })
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  try {
    const res = await fetch(`${base}/open?path=${encodeURIComponent(path)}`, {
      method: "POST",
      headers: authHeaders(),
      cache: "no-store",
      redirect: "manual",
    })
    if (res.status >= 300 && res.status < 400) {
      return NextResponse.json(
        { detail: "Le fichier est sur le stockage objet (URL présignée) — visionneuse locale indisponible." },
        { status: 501 },
      )
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      return NextResponse.json(body, { status: res.status })
    }
    const tampon = await res.arrayBuffer()
    return new NextResponse(tampon, {
      status: 200,
      headers: { "Content-Type": "application/pdf", "Cache-Control": "no-store" },
    })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
