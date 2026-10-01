import { proxyPiece } from "@/lib/piece-proxy"

export const dynamic = "force-dynamic"

export async function GET(request: Request, context: { params: Promise<{ id: string }> }) {
  return proxyPiece(request, context.params, "apercu")
}

export async function HEAD(request: Request, context: { params: Promise<{ id: string }> }) {
  return proxyPiece(request, context.params, "apercu")
}
