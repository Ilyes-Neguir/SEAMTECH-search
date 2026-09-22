"use client"

// Visionneuse PDF (lot D) — pdf.js BUNDLÉ (worker et polices locaux, aucune
// ressource externe : le poste peut être hors ligne). Surligne la zone d'où
// vient chaque valeur — les coordonnées sont celles stockées par le moteur du
// lot B (fiche_champ_extrait.zone : x0/x1/y0/y1 en points PDF, origine
// HAUT-GAUCHE, page en base 0) : c'est le paiement direct de ce travail.

import { useCallback, useEffect, useRef, useState } from "react"

export interface ZoneASurligner {
  page: number // 0-based
  x0: number
  y0: number
  x1: number
  y1: number
}

interface Props {
  chemin: string | null // chemin d'archive absolu (proxy /api/pdf)
  zone: ZoneASurligner | null
}

type PdfjsModule = typeof import("pdfjs-dist")

export function PdfViewer({ chemin, zone }: Props) {
  const canevasRef = useRef<HTMLCanvasElement>(null)
  const [pdfjs, setPdfjs] = useState<PdfjsModule | null>(null)
  const [document_, setDocument] = useState<Awaited<ReturnType<PdfjsModule["getDocument"]>["promise"]> | null>(null)
  const [pageCourante, setPageCourante] = useState(1)
  const [nbPages, setNbPages] = useState(0)
  const [erreur, setErreur] = useState<string | null>(null)
  const [chargement, setChargement] = useState(false)

  // pdf.js se charge côté client uniquement (il touche window).
  useEffect(() => {
    let vivant = true
    import("pdfjs-dist").then((module_) => {
      if (!vivant) return
      module_.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs" // copié dans public/ : hors ligne
      setPdfjs(module_)
    })
    return () => {
      vivant = false
    }
  }, [])

  useEffect(() => {
    if (!pdfjs || !chemin) {
      setDocument(null)
      setNbPages(0)
      return
    }
    let vivant = true
    setChargement(true)
    setErreur(null)
    pdfjs
      .getDocument({ url: `/api/pdf?path=${encodeURIComponent(chemin)}` })
      .promise.then((doc) => {
        if (!vivant) return
        setDocument(doc)
        setNbPages(doc.numPages)
        setPageCourante(zone ? zone.page + 1 : 1)
      })
      .catch((e: unknown) => {
        if (vivant) setErreur(e instanceof Error ? e.message : "Chargement du PDF impossible.")
      })
      .finally(() => {
        if (vivant) setChargement(false)
      })
    return () => {
      vivant = false
    }
  }, [pdfjs, chemin, zone])

  const rendre = useCallback(async () => {
    const canevas = canevasRef.current
    if (!pdfjs || !document_ || !canevas) return
    const page = await document_.getPage(Math.min(Math.max(1, pageCourante), nbPages))
    const echelle = 1.6
    const viewport = page.getViewport({ scale: echelle })
    const contexte = canevas.getContext("2d")
    if (!contexte) return
    canevas.width = viewport.width
    canevas.height = viewport.height
    await page.render({ canvasContext: contexte, viewport, canvas: canevas }).promise
    // Surlignage : zone en points PDF origine haut-gauche → canvas (échelle).
    if (zone && zone.page + 1 === page.pageNumber) {
      contexte.save()
      contexte.fillStyle = "rgba(250, 204, 21, 0.35)"
      contexte.strokeStyle = "rgba(250, 204, 21, 0.9)"
      contexte.lineWidth = 1.5
      const x = zone.x0 * echelle
      const y = zone.y0 * echelle
      const largeur = (zone.x1 - zone.x0) * echelle
      const hauteur = (zone.y1 - zone.y0) * echelle
      contexte.fillRect(x, y, largeur, hauteur)
      contexte.strokeRect(x, y, largeur, hauteur)
      contexte.restore()
    }
  }, [pdfjs, document_, pageCourante, nbPages, zone])

  useEffect(() => {
    rendre()
  }, [rendre])

  if (!chemin) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
        Sélectionnez un champ surlignable — le PDF s&apos;ouvrira ici à la page et à la zone d&apos;où vient la valeur.
      </div>
    )
  }
  if (erreur) {
    return <div className="flex h-full items-center justify-center p-6 text-sm text-destructive">{erreur}</div>
  }
  return (
    <div className="flex h-full flex-col" data-testid="pdf-viewer">
      <div className="flex items-center justify-between border-b border-border px-3 py-2 text-xs text-muted-foreground">
        <span className="truncate font-mono" title={chemin}>
          {chemin}
        </span>
        <span className="flex items-center gap-2">
          <button
            type="button"
            className="rounded border border-border px-2 py-0.5 disabled:opacity-40"
            onClick={() => setPageCourante((p) => Math.max(1, p - 1))}
            disabled={pageCourante <= 1}
          >
            ‹
          </button>
          <span data-testid="pdf-page">
            {pageCourante} / {nbPages || "…"}
          </span>
          <button
            type="button"
            className="rounded border border-border px-2 py-0.5 disabled:opacity-40"
            onClick={() => setPageCourante((p) => Math.min(nbPages, p + 1))}
            disabled={pageCourante >= nbPages}
          >
            ›
          </button>
        </span>
      </div>
      <div className="flex-1 overflow-auto bg-black/30 p-3">
        {chargement && <p className="p-4 text-sm text-muted-foreground">Chargement du PDF…</p>}
        <canvas ref={canevasRef} className="mx-auto block max-w-full bg-white shadow" data-testid="pdf-canvas" />
      </div>
    </div>
  )
}
