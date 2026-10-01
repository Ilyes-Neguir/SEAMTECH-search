import { type NextRequest, NextResponse } from "next/server"
import { backendBase, authHeaders, requireAuthValide } from "@/lib/backend"

export const dynamic = "force-dynamic"

const RESPONSE_HEADERS = ["content-disposition", "content-length", "content-range", "content-type", "etag", "last-modified"]
const PARAMETRES_SIGNATURE = ["X-Amz-Signature", "Signature", "AWSAccessKeyId"]

function urlPresignee(location: string, base: string): URL | null {
  try {
    const url = new URL(location, base)
    if ((url.protocol !== "https:" && url.protocol !== "http:") || url.username || url.password) return null
    if (!PARAMETRES_SIGNATURE.some((parametre) => url.searchParams.has(parametre))) return null
    return url
  } catch {
    return null
  }
}

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string; artifact: string }> }) {
  const denied = await requireAuthValide()
  if (denied) return denied
  const { id, artifact } = await params
  const allowed = ["report_pdf", "report_docx", "source_pdf", "source_excel"]
  if (!allowed.includes(artifact)) {
    return NextResponse.json({ detail: "Type de document inconnu." }, { status: 400 })
  }

  const base = backendBase()
  if (!base) {
    return NextResponse.json({ detail: "Le téléchargement nécessite un backend configuré." }, { status: 503 })
  }

  try {
    const url = `${base}/imports/${encodeURIComponent(id)}/artifacts/${encodeURIComponent(artifact)}`
    const reponseBackend = await fetch(url, {
      headers: authHeaders(),
      cache: "no-store",
      redirect: "manual",
    })
    let upstream = reponseBackend

    if (reponseBackend.status >= 300 && reponseBackend.status < 400) {
      const location = reponseBackend.headers.get("location")
      const cible = location ? urlPresignee(location, url) : null
      if (!cible) {
        return NextResponse.json({ detail: "Le stockage a renvoyé une redirection invalide." }, { status: 502 })
      }
      // Important : ne pas réutiliser authHeaders() pour cet appel. Le lien est
      // déjà signé et le jeton de service ne doit jamais quitter le backend.
      upstream = await fetch(cible, { cache: "no-store", redirect: "follow" })
    }

    if (upstream.status === 501) {
      return NextResponse.json({ detail: "Le document ne peut pas être servi pour le moment." }, { status: 502 })
    }
    if (!upstream.ok) {
      return NextResponse.json(
        { detail: upstream.status === 404 ? "Le document demandé est introuvable." : "Le téléchargement est temporairement indisponible." },
        { status: upstream.status },
      )
    }

    const headers = new Headers({ "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" })
    for (const name of RESPONSE_HEADERS) {
      const value = upstream.headers.get(name)
      if (value) headers.set(name, value)
    }
    if (!headers.has("content-disposition")) {
      const ext = artifact === "report_pdf" || artifact === "source_pdf" ? "pdf" : artifact === "report_docx" ? "docx" : "xlsx"
      headers.set("Content-Disposition", `attachment; filename="${artifact}.${ext}"`)
    }
    return new NextResponse(upstream.body, { status: upstream.status, headers })
  } catch {
    return NextResponse.json({ detail: "Le backend SEAMTECH est injoignable." }, { status: 502 })
  }
}
