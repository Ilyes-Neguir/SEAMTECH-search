"use client"

import Link from "next/link"
import { useCallback, useEffect, useState } from "react"
import { ChevronLeft, ChevronRight, FileArchive, FileText, Search, X } from "lucide-react"
import { VisionneusePiece } from "@/components/visionneuse-piece"
import { authedFetch } from "@/lib/authed-fetch"
import type { PieceJointe, PiecesArchive } from "@/lib/fiche"
import { cn } from "@/lib/utils"

const PAGE_SIZE = 50

function tailleLisible(octets: number | null): string {
  if (octets == null) return "—"
  if (octets < 1024) return `${octets} o`
  if (octets < 1024 * 1024) return `${(octets / 1024).toFixed(1)} Ko`
  return `${(octets / (1024 * 1024)).toFixed(1)} Mo`
}

function iconePiece(piece: PieceJointe) {
  if (piece.kind === "pdf") return "PDF"
  if (piece.kind === "excel") return "TABLEUR"
  if (piece.kind === "machine") return "CAO"
  return piece.extension.replace(/^\./, "").toUpperCase() || "FICHIER"
}

export function ArchiveBrowser() {
  const [terme, setTerme] = useState("")
  const [extension, setExtension] = useState("")
  const [dossier, setDossier] = useState("")
  const [offset, setOffset] = useState(0)
  const [reponse, setReponse] = useState<PiecesArchive | null>(null)
  const [pieceActive, setPieceActive] = useState<PieceJointe | null>(null)
  const [chargement, setChargement] = useState(true)
  const [erreur, setErreur] = useState<string | null>(null)

  const charger = useCallback(async (signal?: AbortSignal) => {
    setChargement(true)
    setErreur(null)
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) })
    if (terme.trim()) params.set("q", terme.trim())
    if (extension) params.set("extension", extension)
    if (dossier) params.set("dossier", dossier)
    try {
      const response = await authedFetch(`/api/pieces?${params.toString()}`, { signal, cache: "no-store" })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(String(body?.detail ?? `Erreur ${response.status}`))
      const catalogue = body as PiecesArchive
      setReponse(catalogue)
      setPieceActive((active) => catalogue.pieces.find((piece) => piece.id === active?.id) ?? catalogue.pieces[0] ?? null)
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return
      setErreur(error instanceof Error ? error.message : "Le catalogue est momentanément indisponible.")
    } finally {
      if (!signal?.aborted) setChargement(false)
    }
  }, [terme, extension, dossier, offset])

  useEffect(() => {
    const controller = new AbortController()
    const timer = setTimeout(() => { void charger(controller.signal) }, terme ? 220 : 0)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [charger, terme])

  function effacerFiltres() {
    setTerme("")
    setExtension("")
    setDossier("")
    setOffset(0)
  }

  return (
    <main className="flex min-h-[calc(100vh-7rem)] flex-col" data-testid="archive-browser">
      <header className="border-b border-border bg-card/30 px-4 py-4 sm:px-6">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-primary">Consultation documentaire</p>
            <h1 className="mt-1 text-xl font-semibold">Fichiers de l’archive</h1>
            <p className="mt-1 max-w-2xl text-xs text-muted-foreground">Recherchez, prévisualisez et téléchargez les documents autorisés. Les emplacements de stockage restent masqués.</p>
          </div>
          <span className="rounded-full border border-border px-3 py-1 text-xs text-muted-foreground" data-testid="archive-count">
            {reponse ? `${reponse.total.toLocaleString("fr-FR")} fichier(s)` : chargement ? "Chargement…" : "Catalogue"}
          </span>
        </div>
        <div className="mx-auto mt-4 grid max-w-[1600px] grid-cols-1 gap-2 sm:grid-cols-[minmax(16rem,1fr)_12rem_12rem_auto]">
          <label className="relative block">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <input
              value={terme}
              onChange={(event) => { setTerme(event.target.value); setOffset(0) }}
              placeholder="Nom de fichier…"
              aria-label="Rechercher un fichier"
              className="h-10 w-full rounded border border-border bg-input pl-9 pr-3 text-sm outline-none focus:border-primary"
              data-testid="fichiers-recherche"
            />
          </label>
          <select value={extension} onChange={(event) => { setExtension(event.target.value); setOffset(0) }} aria-label="Filtrer par format" className="h-10 rounded border border-border bg-input px-3 text-sm">
            <option value="">Tous les formats</option>
            {(reponse?.extensions ?? []).map((value) => <option key={value} value={value}>{value.replace(/^\./, "").toUpperCase()}</option>)}
          </select>
          <select value={dossier} onChange={(event) => { setDossier(event.target.value); setOffset(0) }} aria-label="Filtrer par dossier" className="h-10 rounded border border-border bg-input px-3 text-sm">
            <option value="">Tous les dossiers</option>
            {(reponse?.dossiers ?? []).filter(Boolean).map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
          <button type="button" onClick={effacerFiltres} disabled={!terme && !extension && !dossier} className="inline-flex h-10 items-center justify-center gap-2 rounded border border-border px-3 text-xs text-muted-foreground hover:text-foreground disabled:opacity-40" data-testid="effacer-filtres">
            <X className="size-3.5" /> Effacer
          </button>
        </div>
      </header>

      {erreur && <div role="alert" className="border-b border-destructive/40 bg-destructive/10 px-6 py-3 text-sm text-destructive">{erreur}</div>}
      <div className="mx-auto grid min-h-0 w-full max-w-[1600px] flex-1 grid-cols-1 lg:grid-cols-[minmax(20rem,0.8fr)_minmax(0,1.2fr)]">
        <section className={cn("min-h-[24rem] border-b border-border lg:border-b-0 lg:border-r", pieceActive && "hidden lg:block")} aria-label="Résultats du catalogue">
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Documents</h2>
            <span className="text-[10px] text-muted-foreground">{reponse ? `${offset + 1}–${Math.min(offset + PAGE_SIZE, reponse.total)} sur ${reponse.total}` : ""}</span>
          </div>
          <div className="max-h-[calc(100vh-16rem)] overflow-y-auto">
            {chargement && !reponse && <p className="p-6 text-center text-sm text-muted-foreground">Chargement du catalogue…</p>}
            {!chargement && reponse?.pieces.length === 0 && <p className="p-8 text-center text-sm text-muted-foreground">Aucun fichier ne correspond à ces filtres.</p>}
            <ul className="divide-y divide-border/60">
              {(reponse?.pieces ?? []).map((piece) => (
                <li key={piece.id} className={cn("flex items-center gap-2 px-4 transition-colors hover:bg-accent/40", pieceActive?.id === piece.id && "bg-primary/10")}>
                  <button type="button" onClick={() => setPieceActive(piece)} className="flex min-w-0 flex-1 items-center gap-3 py-3 text-left" data-testid={`archive-piece-${piece.id}`}>
                    <span className="flex size-9 shrink-0 items-center justify-center rounded border border-border bg-background text-[8px] font-bold tracking-tight text-primary">{piece.kind === "pdf" ? <FileText className="size-4" /> : <FileArchive className="size-4" />}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-medium" title={piece.name}>{piece.name}</span>
                      <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-muted-foreground">
                        <span>{iconePiece(piece)}</span><span>{tailleLisible(piece.size)}</span>{piece.dossier && <span>· {piece.dossier}</span>}
                      </span>
                    </span>
                  </button>
                  {piece.fiche_code && <Link href={`/fiches/${encodeURIComponent(piece.fiche_code)}`} className="shrink-0 rounded border border-border px-2 py-1 text-[10px] text-muted-foreground hover:border-primary hover:text-primary">Fiche {piece.fiche_code}</Link>}
                </li>
              ))}
            </ul>
          </div>
          <footer className="flex items-center justify-between border-t border-border px-4 py-3">
            <button type="button" onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))} disabled={offset === 0 || chargement} className="inline-flex items-center gap-1 rounded border border-border px-2.5 py-1.5 text-xs disabled:opacity-40"><ChevronLeft className="size-3.5" /> Précédent</button>
            <span className="text-[10px] text-muted-foreground">Page {Math.floor(offset / PAGE_SIZE) + 1}</span>
            <button type="button" onClick={() => setOffset((value) => value + PAGE_SIZE)} disabled={!reponse?.has_more || chargement} className="inline-flex items-center gap-1 rounded border border-border px-2.5 py-1.5 text-xs disabled:opacity-40">Suivant <ChevronRight className="size-3.5" /></button>
          </footer>
        </section>
        <section className={cn("min-h-[24rem]", !pieceActive && "hidden lg:block")}>
          {pieceActive ? <VisionneusePiece piece={pieceActive} /> : <div className="flex h-full min-h-64 items-center justify-center p-8 text-center text-sm text-muted-foreground">Choisissez un document dans le catalogue pour l’ouvrir.</div>}
          {pieceActive && <button type="button" onClick={() => setPieceActive(null)} className="absolute left-4 top-2 z-20 rounded border border-border bg-background px-2 py-1 text-xs lg:hidden"><ChevronLeft className="mr-1 inline size-3.5" />Retour aux fichiers</button>}
        </section>
      </div>
    </main>
  )
}
