"use client"

/**
 * État vide réutilisable (Phase 4).
 *
 * Voile décorative (3D WebGL ou plan SVG en fallback) + message.
 * Purement visuel : le texte reste celui fourni par l'appelant
 * (contrat de gel — aucun sens modifié).
 */

import type { ReactNode } from "react"
import { VoileDecor } from "@/components/voile-decor"

export function EtatVide({ message, action }: { message: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 px-6 py-12 text-center">
      <VoileDecor className="h-36 w-28 text-primary/50" force={0.6} />
      <p className="max-w-sm text-sm leading-relaxed text-muted-foreground">{message}</p>
      {action}
    </div>
  )
}
