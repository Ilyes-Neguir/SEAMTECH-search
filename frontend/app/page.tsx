import { redirect } from "next/navigation"

// Accueil : l'entrée historique de la Phase 0 (recherche fichiers). La
// recherche hybride des fiches vit sur /recherche (lot E).
export const dynamic = "force-dynamic"

export default function Page() {
  redirect("/fichiers")
}
