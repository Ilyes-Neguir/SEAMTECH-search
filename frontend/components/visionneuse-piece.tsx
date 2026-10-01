"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { Download, ExternalLink, Minus, Plus, RotateCw } from "lucide-react"
import type { PieceJointe } from "@/lib/fiche"

export interface ZoneASurligner {
  page: number
  x0: number
  y0: number
  x1: number
  y1: number
}

interface Props {
  piece: PieceJointe | null
  zone?: ZoneASurligner | null
  donneesPrechargees?: Uint8Array | null
}

type PdfjsModule = typeof import("pdfjs-dist")
type PdfDocument = Awaited<ReturnType<PdfjsModule["getDocument"]>["promise"]>

const PREVIEW_URL = (id: number) => `/api/pieces/${encodeURIComponent(id)}/apercu`
const DOWNLOAD_URL = (id: number) => `/api/pieces/${encodeURIComponent(id)}/telecharger`

function messageErreur(status: number, detail: string): string {
  if (status === 404) return "Le fichier n’est plus disponible dans l’archive."
  if (status === 415) return "L’aperçu n’est pas disponible pour ce format. Vous pouvez télécharger le fichier."
  if (status === 401 || status === 403) return "Votre session ne permet pas d’ouvrir ce fichier. Reconnectez-vous puis réessayez."
  return detail || "Impossible de charger l’aperçu pour le moment."
}

export function VisionneusePiece({ piece, zone = null, donneesPrechargees = null }: Props) {
  const canevasRef = useRef<HTMLCanvasElement>(null)
  const renderRef = useRef<{ cancel: () => void } | null>(null)
  const [pdfjs, setPdfjs] = useState<PdfjsModule | null>(null)
  const [documentPdf, setDocumentPdf] = useState<PdfDocument | null>(null)
  const [chargement, setChargement] = useState(false)
  const [erreur, setErreur] = useState<string | null>(null)
  const [texte, setTexte] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [nbPages, setNbPages] = useState(0)
  const [zoom, setZoom] = useState(1.25)
  const [tentative, setTentative] = useState(0)

  useEffect(() => {
    let actif = true
    import("pdfjs-dist").then((module) => {
      if (!actif) return
      module.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs"
      setPdfjs(module)
    }).catch(() => {
      if (actif) setErreur("Le lecteur PDF n’a pas pu démarrer.")
    })
    return () => { actif = false }
  }, [])

  useEffect(() => {
    renderRef.current?.cancel()
    setDocumentPdf(null)
    setNbPages(0)
    setTexte(null)
    setErreur(null)
    setPage(zone ? zone.page + 1 : 1)
    if (!piece || !piece.previewable) {
      setChargement(false)
      return
    }

    if (piece.kind === "pdf") {
      if (!pdfjs) return
      let actif = true
      setChargement(true)
      const source = donneesPrechargees
        ? { data: donneesPrechargees.slice() }
        : { url: PREVIEW_URL(piece.id), withCredentials: true, rangeChunkSize: 65536 }
      const task = pdfjs.getDocument(source as Parameters<PdfjsModule["getDocument"]>[0])
      task.promise.then((doc) => {
        if (!actif) return
        setDocumentPdf(doc)
        setNbPages(doc.numPages)
        setPage(zone ? zone.page + 1 : 1)
      }).catch((error: unknown) => {
        if (!actif) return
        const detail = error instanceof Error ? error.message : "Le document PDF est illisible."
        setErreur(messageErreur(0, detail))
      }).finally(() => {
        if (actif) setChargement(false)
      })
      return () => {
        actif = false
        void task.destroy()
      }
    }

    if (piece.kind === "other" && [".txt", ".csv", ".md", ".log", ".json", ".xml"].includes(piece.extension.toLowerCase())) {
      let actif = true
      setChargement(true)
      fetch(PREVIEW_URL(piece.id), { cache: "no-store" }).then(async (response) => {
        if (!response.ok) {
          const body = await response.json().catch(() => ({}))
          throw Object.assign(new Error(String(body?.detail ?? "Aperçu indisponible.")), { status: response.status })
        }
        return response.text()
      }).then((content) => {
        if (actif) setTexte(content)
      }).catch((error: unknown) => {
        if (!actif) return
        const status = typeof error === "object" && error !== null && "status" in error ? Number(error.status) : 0
        setErreur(messageErreur(status, error instanceof Error ? error.message : "Aperçu indisponible."))
      }).finally(() => {
        if (actif) setChargement(false)
      })
      return () => { actif = false }
    }

    setChargement(false)
    return undefined
  }, [piece?.id, piece?.extension, piece?.kind, piece?.previewable, pdfjs, donneesPrechargees, tentative, zone])

  const rendrePage = useCallback(async () => {
    if (!pdfjs || !documentPdf || !canevasRef.current) return
    renderRef.current?.cancel()
    const canevas = canevasRef.current
    try {
      const pagePdf = await documentPdf.getPage(Math.min(Math.max(1, page), nbPages))
      const viewport = pagePdf.getViewport({ scale: zoom })
      const contexte = canevas.getContext("2d")
      if (!contexte) return
      canevas.width = Math.ceil(viewport.width)
      canevas.height = Math.ceil(viewport.height)
      const rendu = pagePdf.render({ canvasContext: contexte, viewport, canvas: canevas })
      renderRef.current = rendu
      await rendu.promise
      if (zone && zone.page + 1 === pagePdf.pageNumber) {
        contexte.save()
        contexte.fillStyle = "rgba(85, 185, 170, 0.30)"
        contexte.strokeStyle = "rgba(85, 185, 170, 0.95)"
        contexte.lineWidth = 1.5
        const x = zone.x0 * zoom
        const y = zone.y0 * zoom
        const width = (zone.x1 - zone.x0) * zoom
        const height = (zone.y1 - zone.y0) * zoom
        contexte.fillRect(x, y, width, height)
        contexte.strokeRect(x, y, width, height)
        contexte.restore()
      }
    } catch (error) {
      if (error instanceof Error && error.name === "RenderingCancelledException") return
      setErreur("Cette page du PDF n’a pas pu être affichée.")
    }
  }, [pdfjs, documentPdf, page, nbPages, zoom, zone])

  useEffect(() => {
    void rendrePage()
    return () => renderRef.current?.cancel()
  }, [rendrePage])

  if (!piece) {
    return (
      <div className="flex h-full min-h-64 items-center justify-center p-6 text-center text-sm text-muted-foreground">
        Sélectionnez un fichier pour en afficher l’aperçu.
      </div>
    )
  }

  const apercuUrl = PREVIEW_URL(piece.id)
  const telechargerUrl = DOWNLOAD_URL(piece.id)
  const estImage = piece.kind === "other" && [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"].includes(piece.extension.toLowerCase())
  const estTexte = piece.kind === "other" && [".txt", ".csv", ".md", ".log", ".json", ".xml"].includes(piece.extension.toLowerCase())

  return (
    <section className="flex h-full min-h-64 flex-col bg-background/40" data-testid="visionneuse-piece">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-semibold" title={piece.name}>{piece.name}</p>
          <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{piece.extension.replace(/^\./, "") || "Fichier"}</p>
        </div>
        <div className="flex items-center gap-1.5">
          {piece.kind === "pdf" && !erreur && (
            <>
              <button type="button" onClick={() => setZoom((value) => Math.max(0.5, Number((value - 0.15).toFixed(2))))} className="rounded border border-border p-1.5" aria-label="Réduire le zoom" title="Réduire le zoom"><Minus className="size-3.5" /></button>
              <span className="min-w-10 text-center text-[10px] text-muted-foreground">{Math.round(zoom * 100)}%</span>
              <button type="button" onClick={() => setZoom((value) => Math.min(3, Number((value + 0.15).toFixed(2))))} className="rounded border border-border p-1.5" aria-label="Augmenter le zoom" title="Augmenter le zoom"><Plus className="size-3.5" /></button>
              <span className="mx-1 h-4 border-l border-border" />
              <button type="button" onClick={() => setPage((value) => Math.max(1, value - 1))} disabled={page <= 1} className="rounded border border-border px-2 py-1 text-xs disabled:opacity-40" aria-label="Page précédente">‹</button>
              <span className="text-[10px] tabular-nums" data-testid="pdf-page">{page} / {nbPages || "…"}</span>
              <button type="button" onClick={() => setPage((value) => Math.min(nbPages, value + 1))} disabled={page >= nbPages} className="rounded border border-border px-2 py-1 text-xs disabled:opacity-40" aria-label="Page suivante">›</button>
            </>
          )}
          {piece.previewable && <a href={apercuUrl} target="_blank" rel="noreferrer" className="rounded border border-border p-1.5 text-muted-foreground hover:text-foreground" aria-label="Ouvrir l’aperçu dans un nouvel onglet" title="Ouvrir dans un nouvel onglet"><ExternalLink className="size-3.5" /></a>}
          <a href={telechargerUrl} className="rounded border border-border p-1.5 text-muted-foreground hover:text-foreground" aria-label="Télécharger le fichier" title="Télécharger"><Download className="size-3.5" /></a>
        </div>
      </header>

      <div className="relative min-h-0 flex-1 overflow-auto bg-black/20 p-3">
        {chargement && <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/70 text-sm text-muted-foreground">Chargement de l’aperçu…</div>}
        {erreur && (
          <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-background/90 p-6 text-center">
            <p role="alert" className="max-w-md text-sm text-muted-foreground">{erreur}</p>
            <div className="flex flex-wrap justify-center gap-2">
              <button type="button" onClick={() => setTentative((value) => value + 1)} className="inline-flex items-center gap-2 rounded border border-border px-3 py-2 text-xs font-medium hover:bg-accent"><RotateCw className="size-3.5" /> Réessayer</button>
              {piece.kind === "pdf" && <a href={telechargerUrl} className="rounded bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground">Télécharger le PDF</a>}
            </div>
          </div>
        )}
        {!piece.previewable && !erreur && !chargement && <p className="p-6 text-center text-sm text-muted-foreground">L’aperçu n’est pas disponible pour ce format. Téléchargez le fichier pour l’ouvrir.</p>}
        {piece.kind === "pdf" && <canvas ref={canevasRef} className="mx-auto block max-w-full bg-white shadow" data-testid="pdf-canvas" data-zone-active={zone ? "true" : "false"} data-zone-page={zone?.page} />}
        {estImage && <img src={apercuUrl} alt={`Aperçu de ${piece.name}`} className="mx-auto block max-h-full max-w-full object-contain" style={{ transform: `scale(${zoom})`, transformOrigin: "top center" }} />}
        {estTexte && texte !== null && <pre className="mx-auto max-w-4xl whitespace-pre-wrap break-words rounded bg-background p-4 font-mono text-xs leading-relaxed">{texte}</pre>}
      </div>
    </section>
  )
}
