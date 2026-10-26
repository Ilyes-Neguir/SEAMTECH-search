import { redirect } from "next/navigation"
import { ExtractionsAccueil } from "@/components/extractions-accueil"
import { isAuthenticated } from "@/lib/auth"

export const dynamic = "force-dynamic"

export default async function Page() {
  if (!(await isAuthenticated())) redirect("/login?next=/extractions")
  // Lot 7 : accueil à filtre famille (Voiles | Accessoires) — l'écran voiles
  // Lot 3b reste inchangé derrière l'onglet par défaut.
  return <ExtractionsAccueil />
}
