import { redirect } from "next/navigation"
import { SearchApp } from "@/components/search-app"
import { isAuthenticated } from "@/lib/auth"

// Écran Recherche (lot D) : consomme l'existant (Phase 0) — sera rebranché sur
// la recherche hybride /recherche au lot E (§17.6).
export const dynamic = "force-dynamic"

export default async function Page() {
  if (!(await isAuthenticated())) redirect("/login?next=/recherche")
  return <SearchApp />
}
