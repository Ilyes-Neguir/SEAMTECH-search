"use client"

import { useEffect, useState } from "react"
import { useSearchParams } from "next/navigation"
import { Download, FileText } from "lucide-react"
import { PalierBadge } from "@/components/palier-badge"
import { VisionneusePiece, type ZoneASurligner } from "@/components/visionneuse-piece"
import { authedFetch } from "@/lib/authed-fetch"
import { palierDeChamp, type ChampExtrait, type FicheDetail, type HistoriqueFiche, type PieceJointe, type PiecesDeFiche } from "@/lib/fiche"
import { cn } from "@/lib/utils"

async function jsonFetch<T>(url: string): Promise<T> {
  const res = await authedFetch(url)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(String(body?.detail ?? `HTTP ${res.status}`))
  return body as T
}

function tailleLisible(octets: number | null): string {
  if (octets == null) return "Taille inconnue"
  if (octets < 1024) return `${octets} o`
  if (octets < 1024 * 1024) return `${(octets / 1024).toFixed(1)} Ko`
  return `${(octets / (1024 * 1024)).toFixed(1)} Mo`
}

export function FicheApp({ code }: { code: string }) {
  const [detail, setDetail] = useState<FicheDetail | null>(null)
  const [champs, setChamps] = useState<ChampExtrait[]>([])
  const [pieces, setPieces] = useState<PiecesDeFiche | null>(null)
  const [journal, setJournal] = useState<HistoriqueFiche[]>([])
  const [pieceActive, setPieceActive] = useState<PieceJointe | null>(null)
  const [zone, setZone] = useState<ZoneASurligner | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)
  const parametres = useSearchParams()

  useEffect(() => {
    setErreur(null)
    setPieceActive(null)
    jsonFetch<FicheDetail>(`/api/fiches/${encodeURIComponent(code)}`).then(setDetail).catch((e) => setErreur(e.message))
    jsonFetch<ChampExtrait[]>(`/api/fiches/${encodeURIComponent(code)}/champs`).then(setChamps).catch((e) => setErreur(e.message))
    jsonFetch<PiecesDeFiche>(`/api/fiches/${encodeURIComponent(code)}/pieces`).then((corps) => {
      setPieces(corps)
      setPieceActive(corps.pieces.find((piece) => piece.is_primary_pdf) ?? corps.pieces.find((piece) => piece.kind === "pdf") ?? corps.pieces[0] ?? null)
    }).catch((e) => setErreur(e.message))
    jsonFetch<HistoriqueFiche[]>(`/api/fiches/${encodeURIComponent(code)}/historique`).then(setJournal).catch(() => setJournal([]))
  }, [code])

  useEffect(() => {
    const champCible = parametres.get("champ")
    if (!champCible) return
    const rangCible = parametres.get("rang")
    const trouve = champs.find(
      (champ) => champ.champ === champCible && (rangCible == null || String(champ.rang ?? "") === rangCible),
    )
    if (trouve?.zone) {
      setZone({ page: trouve.zone.page, x0: trouve.zone.x0, y0: trouve.zone.y0, x1: trouve.zone.x1, y1: trouve.zone.y1 })
    }
  }, [parametres, champs])

  return (
    <div className="grid h-[calc(100vh-3.5rem)] min-h-0 grid-cols-[minmax(20rem,0.9fr)_minmax(0,1.1fr)]" data-testid="fiche-app">
      <section className="min-h-0 overflow-auto">
        <header className="border-b border-border p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">Détail de la fiche</p>
              <h1 className="mt-1 font-mono text-lg font-bold" data-testid="fiche-code">{code}</h1>
              {detail && <p className="mt-1 text-sm text-muted-foreground">{detail.titre || "Sans titre"}</p>}
            </div>
            {detail && <span className="rounded-full border border-border px-2.5 py-1 text-[10px] uppercase tracking-wider">{detail.statut}</span>}
          </div>
          {erreur && <p role="alert" className="mt-2 text-xs text-destructive">{erreur}</p>}
          {detail && (
            <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
              <div><dt className="text-muted-foreground">Client</dt><dd className="mt-0.5 font-medium">{detail.client || "—"}</dd></div>
              <div><dt className="text-muted-foreground">Bateau</dt><dd className="mt-0.5 font-medium">{[detail.bateau, detail.bateau_taille].filter(Boolean).join(" · ") || "—"}</dd></div>
              <div><dt className="text-muted-foreground">Gabarit</dt><dd className="mt-0.5 font-medium">{detail.gabarit || "—"}</dd></div>
              <div><dt className="text-muted-foreground">Édition</dt><dd className="mt-0.5 font-medium">{detail.date_edition || "—"}</dd></div>
            </dl>
          )}
        </header>

        <section className="border-b border-border p-4">
          <div className="mb-2 flex items-center justify-between gap-2">
            <h2 className="text-sm font-semibold">Fichiers de la fiche</h2>
            <span className="text-[10px] text-muted-foreground">{pieces?.pieces.length ?? 0} fichier(s)</span>
          </div>
          {pieces?.pieces.length ? (
            <ul className="space-y-1" data-testid="pieces-jointes">
              {pieces.pieces.map((piece) => (
                <li key={piece.id}>
                  <div className={cn("flex items-center gap-2 rounded border px-2.5 py-1.5 transition-colors", pieceActive?.id === piece.id ? "border-primary/60 bg-primary/10" : "border-transparent hover:border-border hover:bg-accent/40")}>
                    <button type="button" onClick={() => setPieceActive(piece)} className="flex min-w-0 flex-1 items-center gap-2 text-left text-xs" data-testid={`piece-${piece.id}`}>
                      <FileText className="size-4 shrink-0 text-primary" />
                      <span className="min-w-0 flex-1 truncate font-medium" title={piece.name}>{piece.name}</span>
                      <span className="shrink-0 text-[10px] text-muted-foreground">{tailleLisible(piece.size)}</span>
                      {piece.is_primary_pdf && <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[9px] text-primary">PDF source</span>}
                    </button>
                    <a href={`/api/pieces/${piece.id}/telecharger`} className="rounded p-1 text-muted-foreground hover:text-foreground" aria-label={`Télécharger ${piece.name}`} title="Télécharger"><Download className="size-3.5" /></a>
                  </div>
                </li>
              ))}
            </ul>
          ) : <p className="text-xs text-muted-foreground">Aucun fichier catalogué pour cette fiche.</p>}
        </section>

        <section className="border-b border-border">
          <h2 className="px-4 pb-2 pt-4 text-sm font-semibold">Champs extraits</h2>
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
                    <span className="max-w-32 truncate font-mono text-[10px] text-muted-foreground" title={`brute : ${champ.valeur_brute ?? "—"}`}>{champ.valeur_brute ?? "—"}</span>
                    <PalierBadge palier={palierDeChamp(champ.confiance)} corrige={champ.corrige} />
                    {champ.zone && <button type="button" onClick={() => setZone({ page: champ.zone!.page, x0: champ.zone!.x0, y0: champ.zone!.y0, x1: champ.zone!.x1, y1: champ.zone!.y1 })} className="text-muted-foreground hover:text-foreground" aria-label={`Voir la zone de ${champ.champ}`}><FileText className="size-3.5" /></button>}
                  </span>
                </div>
              )
            })}
            {champs.length === 0 && <p className="px-4 pb-4 text-xs text-muted-foreground">Aucun champ extrait.</p>}
          </div>
        </section>

        <section className="p-4" data-testid="historique-fiche">
          <h2 className="mb-2 text-sm font-semibold">Historique des validations</h2>
          {journal.length ? (
            <ol className="space-y-2">
              {journal.map((ligne, i) => (
                <li key={`${ligne.created_at}-${i}`} className="border-l-2 border-primary/40 pl-3 text-xs">
                  <p className="font-medium">{ligne.action}{ligne.etat_apres ? ` · ${ligne.etat_apres}` : ""}</p>
                  <p className="text-[10px] text-muted-foreground">{new Date(ligne.created_at).toLocaleString("fr-FR")}{ligne.commentaire ? ` · ${ligne.commentaire}` : ""}</p>
                </li>
              ))}
            </ol>
          ) : <p className="text-xs text-muted-foreground">Aucune décision de validation enregistrée.</p>}
        </section>
      </section>

      <aside className="min-h-0 border-l border-border">
        <VisionneusePiece piece={pieceActive} zone={zone} />
      </aside>
    </div>
  )
}
