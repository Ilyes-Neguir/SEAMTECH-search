"use client"

// Badge de PALIER ORDINAL (§4 CONTROLES_RG16) : des comptes et des étiquettes,
// JAMAIS une « confiance moyenne ». Les couleurs portent la décision, pas un
// pseudo-probabiliste.

import { cn } from "@/lib/utils"

const STYLES: Record<string, string> = {
  certain: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  lu: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  decompose: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  partiel: "bg-orange-600/15 text-orange-300 border-orange-600/30",
  non_note: "bg-white/5 text-muted-foreground border-border",
  corrige: "bg-violet-500/15 text-violet-300 border-violet-500/30",
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
