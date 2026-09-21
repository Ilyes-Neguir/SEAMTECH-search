import { redirect } from "next/navigation"

// Accueil : la porte d'entrée est l'écran Recherche (lot D).
export const dynamic = "force-dynamic"

export default function Page() {
  redirect("/recherche")
}
