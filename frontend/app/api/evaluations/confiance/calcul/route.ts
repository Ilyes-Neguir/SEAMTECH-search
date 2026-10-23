import { NextRequest, NextResponse } from "next/server";
import { requireAuthValide } from "@/lib/auth-helpers";

export const dynamic = "force-dynamic";
const backendBase = process.env.SEAMTECH_API_URL || "http://localhost:8000";

export async function POST(req: NextRequest) {
  const auth = await requireAuthValide();
  if (!auth.ok) return NextResponse.json({ detail: "Non authentifié" }, { status: 401, headers: { "Cache-Control": "no-store" } });
  const headers: Record<string, string> = { "Content-Type": "application/json", "X-SEAMTECH-TOKEN": process.env.SEAMTECH_AUTH_TOKEN || "" };
  if (auth.sessionId) headers["X-SEAMTECH-SESSION"] = auth.sessionId;
  if (auth.user?.role) headers["X-Role"] = auth.user.role;
  if (auth.user?.id) headers["X-User-Id"] = String(auth.user.id);
  const body = await req.text();
  try {
    const resp = await fetch(`${backendBase}/evaluations/confiance/calcul`, { method: "POST", headers, body, cache: "no-store" });
    const text = await resp.text();
    return new NextResponse(text, { status: resp.status, headers: { "Content-Type": resp.headers.get("Content-Type") || "application/json", "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ detail: "Backend indisponible" }, { status: 502, headers: { "Cache-Control": "no-store" } });
  }
}
