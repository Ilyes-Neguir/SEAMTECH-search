"use client"

// Badge de PALIER ORDINAL (§4 CONTROLES_RG16) : des comptes et des étiquettes,
// JAMAIS une « confiance moyenne ». Les couleurs portent la décision, pas un
// pseudo-probabiliste.

import { cn } from "@/lib/utils"

const STYLES: Record<string, string> = {
  certain: "badge-succes",
  lu: "badge-info",
  decompose: "badge-attention",
  partiel: "badge-attention",
  non_note: "bg-muted text-muted-foreground border-border",
  corrige: "badge-corrige",
}

const LIBELLES: Record<string, string> = {
  certain: "certain 0,99",
  lu: "lu 0,90",
  decompose: "décomposé 0,85",
  partiel: "partiel",
  non_note: "non noté",
  corrige: "corrigé",
}

export function PalierBadge({ palier, corrige = false }: { palier: string; corrige?: boolean }) {
  const cle = corrige ? "corrige" : palier
  return (
    <span
      className={cn("inline-flex shrink-0 items-center rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide", STYLES[cle] ?? STYLES.non_note)}
      data-testid={`palier-${cle}`}
    >
      {LIBELLES[cle] ?? cle}
    </span>
  )
}
