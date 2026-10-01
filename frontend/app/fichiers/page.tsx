import { redirect } from "next/navigation"
import { FichiersPage } from "@/components/fichiers-page"
import { isAuthenticated } from "@/lib/auth"

// Écran Fichiers : recherche de fichiers/dossiers de la Phase 0, déplacée
// ici au lot E pour laisser /recherche à la recherche hybride des fiches.
export const dynamic = "force-dynamic"

export default async function Page() {
  if (!(await isAuthenticated())) redirect("/login?next=/fichiers")
  return <FichiersPage />
}
