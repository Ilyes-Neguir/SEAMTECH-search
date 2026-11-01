"use client"

import { useEffect, useRef, useState } from "react"
import { authedFetch } from "@/lib/authed-fetch"
import { estAdmin } from "@/lib/roles"
import { useSession } from "@/lib/use-session"

interface Brouillon {
  id_brouillon?: number
  code: string
  description: string
  ancres_detection: string[]
  regles: { champs: Array<{ cible: string; ancres: string[]; type: string; confiance: number; zone: { page: number; rectangle: number[] } }> }
  zones: Array<{ champ: string; page: number; rectangle: number[]; confiance: number; ancre: string }>
  confiance: Record<string, number>
  source_pdf_sha256?: string
  source_pdf_nom?: string
  nb_champs_detectes?: number
  nb_champs_total?: number
  champs_detectes?: string[]
  statut?: string
}

interface Gabarit {
  code: string
  version: number
  description: string
  nb_ancres: number
  ancres_detection: string[]
  actif: boolean
  nb_fiches: number
}

export function GabaritsApp() {
  const { role } = useSession()
  const peutAdministrerGabarits = estAdmin(role)
  const [gabarits, setGabarits] = useState<Gabarit[]>([])
  const [brouillons, setBrouillons] = useState<Brouillon[]>([])
  const [brouillonCourant, setBrouillonCourant] = useState<Brouillon | null>(null)
  const [pdfFile, setPdfFile] = useState<File | null>(null)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)

  const charger = async () => {
    const [g, b] = await Promise.all([
      fetch("/api/gabarits").then((r) => (r.ok ? r.json() : [])),
      fetch("/api/gabarits/brouillons").then((r) => (r.ok ? r.json() : [])),
    ])
    setGabarits(g)
    setBrouillons(b)
  }

  useEffect(() => {
    charger()
  }, [])

  const onUpload = async () => {
    if (!pdfFile) return
    setMessage("Génération brouillon…")
    const fd = new FormData()
    fd.append("fichier", pdfFile)
    const r = await authedFetch("/api/gabarits/brouillon/from-pdf", { method: "POST", body: fd })
    if (!r.ok) {
      setMessage(`Erreur: ${await r.text()}`)
      return
    }
    const brouillon = await r.json()
    setBrouillonCourant(brouillon)
    setMessage(`Brouillon généré: ${brouillon.nb_champs_detectes}/${brouillon.nb_champs_total} champs — code ${brouillon.code}`)
    await charger()
  }

  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] || null
    setPdfFile(f)
    if (f) {
      const url = URL.createObjectURL(f)
      setPdfUrl(url)
    }
  }

  // Rendu PDF local avec pdf.js + surlignage zones
  useEffect(() => {
    if (!pdfUrl || !brouillonCourant) return
    let vivant = true
    import("pdfjs-dist").then(async (pdfjs) => {
      // @ts-ignore
      pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs"
      const doc = await pdfjs.getDocument({ url: pdfUrl }).promise
      if (!vivant) return
      const page = await doc.getPage(1)
      const canvas = canvasRef.current
      if (!canvas) return
      const viewport = page.getViewport({ scale: 1.5 })
      const ctx = canvas.getContext("2d")
      if (!ctx) return
      canvas.width = viewport.width
      canvas.height = viewport.height
      await page.render({ canvasContext: ctx, viewport, canvas }).promise
      // Surligne zones du brouillon (page 1)
      ctx.save()
      ctx.fillStyle = "rgba(59,130,246,0.25)"
      ctx.strokeStyle = "rgba(59,130,246,0.9)"
      ctx.lineWidth = 1.2
      for (const z of brouillonCourant.zones || []) {
        if (z.page !== 1) continue
        const [x0, y0, x1, y1] = z.rectangle
        if (x1 === 0 && y1 === 0) continue
        ctx.fillRect(x0 * 1.5, y0 * 1.5, (x1 - x0) * 1.5, (y1 - y0) * 1.5)
        ctx.strokeRect(x0 * 1.5, y0 * 1.5, (x1 - x0) * 1.5, (y1 - y0) * 1.5)
      }
      ctx.restore()
    })
    return () => {
      vivant = false
    }
  }, [pdfUrl, brouillonCourant])

  const publier = async () => {
    if (!brouillonCourant?.id_brouillon) {
      setMessage("Brouillon non enregistré — rechargez")
      return
    }
    const r = await authedFetch(`/api/gabarits/brouillons/${brouillonCourant.id_brouillon}/valider`, { method: "POST" })
    if (!r.ok) {
      setMessage(`Erreur publication: ${await r.text()}`)
      return
    }
    const res = await r.json()
    setMessage(`Publié: ${res.gabarit.code} v${res.gabarit.version} (brouillon ${res.id_brouillon} validé)`)
    await charger()
  }

  return (
    <div className="space-y-6 p-6" data-testid="gabarits-app">
      <h1 className="text-xl font-bold">Gabarits — registre versionné + brouillons</h1>
      <p className="text-xs text-muted-foreground">
        K.2 : depuis PDF variante inconnue → brouillon (champs, zones page+rectangle, confiance). Opérateur ajuste, prévisualise via viewer, enregistre comme nouvelle version dans registre existant /gabarits/{"{code}"}/versions (pas registre parallèle). Garde-fou: brouillon NON validé ne sert jamais extraction.
      </p>

      <section className="rounded border p-4">
        <h2 className="font-semibold">Registre gabarits (actifs)</h2>
        <ul className="mt-2 text-xs">
          {gabarits.map((g) => (
            <li key={`${g.code}-${g.version}`} className="font-mono">
              {g.code} v{g.version} — {g.description} — {g.nb_ancres} ancres — {g.nb_fiches} fiches — actif={String(g.actif)}
            </li>
          ))}
        </ul>
      </section>

      {!peutAdministrerGabarits && (
        <p className="badge-attention rounded px-3 py-2 text-xs" role="alert">
          La création et la publication de gabarits sont réservées au rôle admin. Vous pouvez consulter le registre ci-dessus.
        </p>
      )}

      {peutAdministrerGabarits && (
      <section className="rounded border p-4">
        <h2 className="font-semibold">Créer brouillon depuis PDF variante (§10.2)</h2>
        <div className="mt-2 flex items-center gap-2">
          {/* Nom accessible OBLIGATOIRE (axe « label », critique). */}
          <input
            type="file"
            accept="application/pdf"
            onChange={onFileChange}
            data-testid="input-pdf"
            aria-label="PDF de la variante de gabarit à analyser"
          />
