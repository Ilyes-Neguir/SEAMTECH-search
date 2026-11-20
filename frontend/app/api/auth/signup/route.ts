import { type NextRequest, NextResponse } from "next/server"
import { backendBase, backendHeaders } from "@/lib/backend"

export const dynamic = "force-dynamic"

/**
 * Inscription publique (création de compte).
 *
 * Proxy vers POST /auth/signup du backend. Aucune session requise.
 * Le backend crée le compte avec le rôle lecture (privilèges minimum).
 */
export async function POST(req: NextRequest) {
  const base = backendBase()
  if (!base) return NextResponse.json({ detail: "Backend non configuré (SEAMTECH_API_URL)." }, { status: 503 })
  const corps = await req.json().catch(() => ({}))
  try {
    const res = await fetch(`${base}/auth/signup`, {
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
