import { type NextRequest } from "next/server"
import { proxyMultipartBorne, requireRolesServer } from "@/lib/backend"
import { ROLE_ADMIN, ROLE_APPROBATEUR, ROLE_UTILISATEUR } from "@/lib/roles"

export const dynamic = "force-dynamic"

// Le backend borne l'agrégat à dix fois sa taille de fichier par défaut
// (512 Mio) ; marge ajoutée pour l'encadrement multipart.
const MAX_ENVOI_MULTIPART = (10 * 512 + 16) * 1024 * 1024

export async function POST(req: NextRequest) {
  const denied = await requireRolesServer(ROLE_ADMIN, ROLE_UTILISATEUR, ROLE_APPROBATEUR)
  if (denied) return denied
  return proxyMultipartBorne(req, "/imports/upload", MAX_ENVOI_MULTIPART)
}
