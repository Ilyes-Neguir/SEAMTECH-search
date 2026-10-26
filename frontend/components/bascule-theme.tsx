"use client"

// Bascule de quart (Lot 8) : jour ⇄ nuit. Le choix est persisté dans
// localStorage (« seamtech-quart ») et appliqué avant peinture par le script
// d'amorçage du layout — aucun clignotement, aucun serveur. Les icônes sont
// pilotées par la classe `.dark` (CSS), donc justes dès le premier rendu.

import { useEffect, useState } from "react"
import { MoonStar, Sun } from "lucide-react"

export function BasculeTheme() {
  const [nuit, setNuit] = useState<boolean | null>(null)

  useEffect(() => {
    setNuit(document.documentElement.classList.contains("dark"))
  }, [])

  function basculer() {
    const prochaineNuit = !document.documentElement.classList.contains("dark")
    document.documentElement.classList.toggle("dark", prochaineNuit)
    document.documentElement.style.colorScheme = prochaineNuit ? "dark" : "light"
    try {
      window.localStorage.setItem("seamtech-quart", prochaineNuit ? "nuit" : "jour")
    } catch {
      // Stockage indisponible (navigation privée) : le quart vaut pour la page.
    }
    setNuit(prochaineNuit)
  }

  return (
    <button
      type="button"
      onClick={basculer}
      data-testid="bascule-theme"
      aria-label="Basculer entre le quart de jour et le quart de nuit"
      aria-pressed={nuit === null ? undefined : nuit}
      title="Quart de jour / quart de nuit"
      className="flex shrink-0 items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-[10px] font-medium text-muted-foreground transition-colors hover:border-primary/60 hover:text-foreground"
    >
      <Sun className="size-3.5 dark:hidden" aria-hidden />
      <MoonStar className="hidden size-3.5 dark:block" aria-hidden />
      <span className="hidden dark:inline">Nuit</span>
      <span className="dark:hidden">Jour</span>
    </button>
  )
}
