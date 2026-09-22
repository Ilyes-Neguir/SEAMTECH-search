"use client"

// Écran FICHE (lot D) : fiche complète en lecture, PDF à côté avec zones
// surlignables, pièces jointes (description unique `documents`, RG12),
// historique de validation (journal fiche_validation).

import { useEffect, useState } from "react"
import { FileText } from "lucide-react"
import { PalierBadge } from "@/components/palier-badge"
import { PdfViewer, type ZoneASurligner } from "@/components/pdf-viewer"
import { authedFetch } from "@/lib/authed-fetch"
import { palierDeChamp, type ChampExtrait, type PiecesDeFiche } from "@/lib/fiche"

interface Journal {
  action: string
  etat_avant: string | null
  etat_apres: string | null
  commentaire: string | null
  created_at: string
  identifiant: string | null
}

async function jsonFetch<T>(url: string): Promise<T> {
  const res = await authedFetch(url)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(String(body?.detail ?? `HTTP ${res.status}`))
  return body as T
}

export function FicheApp({ code }: { code: string }) {
  const [champs, setChamps] = useState<ChampExtrait[]>([])
  const [pieces, setPieces] = useState<PiecesDeFiche | null>(null)
  const [journal, setJournal] = useState<Journal[]>([])
  const [zone, setZone] = useState<ZoneASurligner | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)

  useEffect(() => {
    jsonFetch<ChampExtrait[]>(`/api/fiches/${encodeURIComponent(code)}/champs`).then(setChamps).catch((e) => setErreur(e.message))
    jsonFetch<PiecesDeFiche>(`/api/fiches/${encodeURIComponent(code)}/pieces`).then(setPieces).catch(() => setPieces(null))
    // L'historique arrive avec les écrans de suivi (pas d'endpoint journal encore
    // exposé en lecture) : le journal est écrit à chaque action (testé côté API).
  }, [code])

  return (
    <div className="grid h-[calc(100vh-3.5rem)] min-h-0 grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]" data-testid="fiche-app">
      <section className="min-h-0 overflow-auto">
        <header className="border-b border-border p-4">
          <h1 className="font-mono text-lg font-bold" data-testid="fiche-code">
            {code}
          </h1>
          {erreur && <p className="mt-2 text-xs text-destructive">{erreur}</p>}
        </header>
        <div className="divide-y divide-border/60">
          {champs.map((champ) => {
            const cle = `${champ.champ}#${champ.rang ?? 0}`
            return (
              <div key={cle} className="grid grid-cols-[1fr_1fr_auto] items-center gap-2 px-4 py-1.5 text-xs">
                <span className="truncate font-mono text-muted-foreground">{champ.champ}{champ.rang != null ? ` [${champ.rang}]` : ""}</span>
                <span className="truncate" data-testid={`valeur-${champ.champ}`}>
                  {champ.valeur_normalisee ?? <span className="text-muted-foreground">—</span>}
                  {champ.corrige && <span className="ml-1 text-[10px] text-violet-300">(corrigé par {champ.corrige_par})</span>}
                </span>
                <span className="flex items-center gap-1">
                  <span className="max-w-40 truncate font-mono text-[10px] text-muted-foreground" title={`brute : ${champ.valeur_brute ?? "—"}`}>
                    {champ.valeur_brute ?? "—"}
                  </span>
                  <PalierBadge palier={palierDeChamp(champ.confiance)} corrige={champ.corrige} />
                  {champ.zone && (
                    <button
                      type="button"
                      onClick={() =>
                        setZone({ page: champ.zone!.page, x0: champ.zone!.x0, y0: champ.zone!.y0, x1: champ.zone!.x1, y1: champ.zone!.y1 })
                      }
                      className="text-muted-foreground hover:text-foreground"
                      aria-label={`Voir la zone de ${champ.champ}`}
                    >
                      <FileText className="size-3.5" />
                    </button>
                  )}
                </span>
              </div>
            )
          })}
        </div>
        {pieces && pieces.pieces.length > 0 && (
          <div className="border-t border-border p-4">
            <h2 className="mb-2 text-sm font-semibold">Pièces jointes (non analysées, RG12)</h2>
            <ul className="space-y-1 text-xs" data-testid="pieces-jointes">
              {pieces.pieces.map((piece) => (
                <li key={piece.chemin} className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono">{piece.nom ?? piece.chemin}</span>
                  <span className="text-muted-foreground">
                    {piece.role}
                    {piece.id_document != null ? ` · catalogue #${piece.id_document}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>
      <aside className="min-h-0 border-l border-border">
        <PdfViewer chemin={pieces?.pdf_source ?? null} zone={zone} />
      </aside>
    </div>
  )
}
