import { NextResponse } from "next/server"
import { backendBase, backendHeaders, requireAuthValide } from "@/lib/backend"

const RESPONSE_HEADERS = [
  "accept-ranges",
  "cache-control",
  "content-disposition",
  "content-length",
  "content-range",
  "content-type",
  "etag",
  "last-modified",
  "x-content-type-options",
]

/** Session-authenticated streaming proxy for the backend's ID-based file API.
 * The backend streams bytes directly; unexpected redirects are rejected so
 * service/session headers can never be forwarded to an object-storage host. */
export async function proxyPiece(request: Request, params: Promise<{ id: string }>, action: "apercu" | "telecharger") {
  const denied = await requireAuthValide()
  if (denied) return denied
  const { id } = await params
  if (!/^\d+$/.test(id)) return NextResponse.json({ detail: "Identifiant de fichier invalide." }, { status: 404 })

  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })

  const headers = await backendHeaders()
  for (const name of ["range", "if-range"]) {
    const value = request.headers.get(name)
    if (value) headers[name] = value
  }

  try {
    const upstream = await fetch(`${base}/pieces/${encodeURIComponent(id)}/${action}`, {
      method: request.method,
      headers,
      cache: "no-store",
      // The backend streams bytes by id; do not follow a redirect here. In
      // particular, never forward its service/session headers to object storage.
      redirect: "manual",
    })
    if (upstream.status >= 300 && upstream.status < 400) {
      return NextResponse.json({ detail: "Le service de fichiers a renvoyé une redirection inattendue." }, { status: 502 })
    }
    // A file endpoint must never turn a storage limitation into the old opaque
    // 501. Use a normal gateway error with a message the UI can handle.
    if (upstream.status === 501) {
      return NextResponse.json({ detail: "Le fichier ne peut pas être servi pour le moment." }, { status: 502 })
    }

    const responseHeaders = new Headers({ "Cache-Control": "no-store" })
    for (const name of RESPONSE_HEADERS) {
      const value = upstream.headers.get(name)
      if (value) responseHeaders.set(name, value)
    }
    if (request.method === "HEAD") {
      return new NextResponse(null, { status: upstream.status, headers: responseHeaders })
    }
    return new NextResponse(upstream.body, { status: upstream.status, headers: responseHeaders })
  } catch {
    return NextResponse.json({ detail: "Le service de fichiers est temporairement indisponible." }, { status: 502 })
  }
}
