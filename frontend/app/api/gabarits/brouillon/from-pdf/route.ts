import { type NextRequest } from "next/server"
import { proxyMultipartBorne, requireRolesServer } from "@/lib/backend"
import { ROLE_ADMIN } from "@/lib/roles"

export const dynamic = "force-dynamic"

// 20 Mio de PDF côté backend + marge pour l'encadrement multipart.
const MAX_ENVOI_MULTIPART = 21 * 1024 * 1024

export async function POST(req: NextRequest) {
  const denied = await requireRolesServer(ROLE_ADMIN)
  if (denied) return denied
  return proxyMultipartBorne(req, "/gabarits/brouillon/from-pdf", MAX_ENVOI_MULTIPART)
}
