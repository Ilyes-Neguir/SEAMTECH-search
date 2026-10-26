"use client"

// Accueil EXTRACTIONS (Lot 7) — une seule entrée, deux familles. Les voiles
// gardent leur écran Lot 3b à l'identique ; les accessoires ont leur file
// simplifiée (un modèle de bateau). Le filtre famille vit dans l'URL
// (?famille=accessoires) pour être partageable et testable.

import { useEffect, useState } from "react"
import { AccessoiresApp } from "@/components/accessoires-app"
import { ExtractionsApp } from "@/components/extractions-app"
import { cn } from "@/lib/utils"

type Famille = "voiles" | "accessoires"

export function ExtractionsAccueil() {
  const [famille, setFamille] = useState<Famille>("voiles")

  useEffect(() => {
    const lue = new URLSearchParams(window.location.search).get("famille")
    if (lue === "accessoires") setFamille("accessoires")
  }, [])

  function changer(nouvelle: Famille) {
    setFamille(nouvelle)
    const url = new URL(window.location.href)
    if (nouvelle === "accessoires") url.searchParams.set("famille", "accessoires")
    else url.searchParams.delete("famille")
    window.history.replaceState(null, "", url.toString())
  }

  return (
    <div>
      <nav className="flex gap-1 border-b border-border px-3 pt-2" aria-label="Famille d’extractions">
        {(
          [
            ["voiles", "Voiles"],
            ["accessoires", "Accessoires"],
          ] as [Famille, string][]
        ).map(([code, libelle]) => (
          <button
            key={code}
            type="button"
            onClick={() => changer(code)}
            className={cn(
              "rounded-t border border-b-0 border-border px-3 py-1.5 text-xs font-semibold",
              famille === code ? "bg-accent" : "bg-transparent text-muted-foreground hover:text-foreground",
            )}
            data-testid={`famille-${code}`}
            aria-pressed={famille === code}
          >
            {libelle}
          </button>
        ))}
      </nav>
      {famille === "accessoires" ? <AccessoiresApp /> : <ExtractionsApp />}
    </div>
  )
}
