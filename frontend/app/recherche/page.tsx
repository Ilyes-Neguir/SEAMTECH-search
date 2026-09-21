import { redirect } from "next/navigation"
import { RechercheFichesApp } from "@/components/recherche-fiches-app"
import { isAuthenticated } from "@/lib/auth"

// Écran Recherche (lot E) : recherche hybride des fiches techniques.
// L'écran Phase 0 de recherche fichiers vit sur /fichiers.
export const dynamic = "force-dynamic"

export default async function Page() {
  if (!(await isAuthenticated())) redirect("/login?next=/recherche")
  return <RechercheFichesApp />
}
