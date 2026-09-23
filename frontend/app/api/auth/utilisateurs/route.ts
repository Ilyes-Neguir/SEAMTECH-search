import { NextResponse } from "next/server"
import { backendBase, authHeaders, backendHeaders, requireAuthValide } from "@/lib/backend"

export const dynamic = "force-dynamic"

/**
 * Account management proxy (Lot L.2).
 *
 * The browser never talks to the Python API: this route forwards to
 * /auth/utilisateurs with the service token AND the session headers taken from
 * the signed cookie. The backend then decides — an operator gets 403, an
 * administrator 200 — and its decision is passed through unchanged. The
 * frontend deliberately does NOT re-implement the role check: two places
 * deciding the same rule is how they drift apart.
 *
 * No password, hash or salt ever reaches the browser: the backend already
 * refuses to return them, and this route does not add anything to the payload.
 */
export async function GET() {
  const denied = await requireAuthValide()
  if (denied) return denied
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  try {
    const res = await fetch(`${base}/auth/utilisateurs`, { headers: await backendHeaders(), cache: "no-store" })
    return NextResponse.json(await res.json().catch(() => []), { status: res.status })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}

export async function POST(req: Request) {
  const denied = await requireAuthValide()
  if (denied) return denied
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  const corps = await req.json().catch(() => ({}))
  try {
    const res = await fetch(`${base}/auth/utilisateurs`, {
      method: "POST",
      headers: await backendHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(corps ?? {}),
      cache: "no-store",
    })
    return NextResponse.json(await res.json().catch(() => ({})), { status: res.status })
  } catch {
    return NextResponse.json({ detail: "Backend SEAMTECH injoignable." }, { status: 502 })
  }
}
