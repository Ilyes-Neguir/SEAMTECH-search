"use client"

import { useRef, useState, useEffect } from "react"
import type { ImportCorrection, ImportJobPayload, ImportResultPayload, ImportScanPayload } from "@/lib/types"
import { AccesRefuse } from "@/components/acces-refuse"
import { CorrectionForm } from "@/components/correction-form"
import { authedFetch } from "@/lib/authed-fetch"
import { estApprobateur, peutDeposer, ROLE_ADMIN } from "@/lib/roles"
import { useSession } from "@/lib/use-session"

interface DroppedFile {
  file: File
  rel: string
}

// Ne conserver dans l'onglet que l'identifiant opaque et son propriétaire,
// jamais le chemin source, les données extraites ni un jeton de session.
const CLE_SUIVI_IMPORT = "seamtech:suivi-import:v1"
const DELAI_REQUETE_SUIVI_MS = 8_000
const DELAI_REPRISE_SUIVI_MS = 1_500
const ID_IMPORT_VALIDE = /^[A-Za-z0-9_-]{1,128}$/

async function readEntry(entry: any, base: string, out: DroppedFile[]): Promise<void> {
  if (entry.isFile) {
    const fichier: File = await new Promise((resolve, reject) => entry.file(resolve, reject))
    out.push({ file: fichier, rel: base + fichier.name })
  } else if (entry.isDirectory) {
    const reader = entry.createReader()
    let batch: any[]
    do {
      batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject))
      for (const child of batch) await readEntry(child, `${base}${entry.name}/`, out)
    } while (batch.length > 0)
  } else {
    throw new Error("Entrée de dépôt non reconnue")
  }
}

async function filesFromDrop(dt: DataTransfer): Promise<DroppedFile[]> {
  const items = Array.from(dt.items ?? []).filter((item) => !item.kind || item.kind === "file")
  const entries = items.map((item) => item.webkitGetAsEntry?.())
  // `files` peut être aplati ou omettre des sous-dossiers. Ne jamais le
  // présenter comme un dossier complet si certaines entrées sont illisibles.
  if (entries.some((entry) => !entry) || (entries.length === 0 && dt.files?.length)) {
    throw new Error("Entrées de dépôt indisponibles")
  }
  const out: DroppedFile[] = []
  for (const entry of entries) await readEntry(entry, "", out)
  return out
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} o`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} Ko`
  return `${(size / (1024 * 1024)).toFixed(1)} Mo`
}

function formatStage(stage: string): string {
  switch (stage) {
    case "starting":
    case "queued":
      return "Préparation de l’import…"
    case "scanning":
      return "Analyse des fichiers…"
    case "extracting":
      return "Extraction des données techniques et Excel…"
    case "generating_reports":
      return "Génération des rapports PDF et Word…"
    case "uploading":
      return "Envoi vers le stockage documentaire…"
    case "indexing":
      return "Mise à jour de l’index de recherche…"
    case "done":
      return "Terminé"
    default:
      return stage ? `${stage}…` : "Traitement…"
  }
}

function ImportPanelInterne() {
  const { session, role } = useSession()
  const peutRapport = role === ROLE_ADMIN || (session.mode === "nominatif" && estApprobateur(role))
  const peutSource = session.mode === "nominatif" && estApprobateur(role)
  const [sourcePath, setSourcePath] = useState("")
  const [scan, setScan] = useState<ImportScanPayload | null>(null)
  const [selectedPdf, setSelectedPdf] = useState<string | null>(null)
  const [selectedExcel, setSelectedExcel] = useState<string | null>(null)
  const [result, setResult] = useState<ImportResultPayload | null>(null)
  const [activeJob, setActiveJob] = useState<ImportJobPayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [annulationEnCours, setAnnulationEnCours] = useState(false)
  const [erreurSuivi, setErreurSuivi] = useState<string | null>(null)
  const [stockageSuiviIndisponible, setStockageSuiviIndisponible] = useState(false)
  const [idManuel, setIdManuel] = useState("")
  const [dragActive, setDragActive] = useState(false)
  const [lectureDepotEnCours, setLectureDepotEnCours] = useState(false)
  const [dropped, setDropped] = useState<DroppedFile[]>([])
  // Résultat du passage EXPLICITE rapport d'import → fiche (« ça analyse mais
  // rien n'apparaît dans les dossiers ») : message affiché sous les boutons.
  const [creationFiche, setCreationFiche] = useState<string | null>(null)
  const filesInput = useRef<HTMLInputElement>(null)
  const folderInput = useRef<HTMLInputElement>(null)
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pollAbortRef = useRef<AbortController | null>(null)
  const pollGenerationRef = useRef(0)
  const annulationRef = useRef(false)
  const annulationAbortRef = useRef<AbortController | null>(null)
  const operationGenerationRef = useRef(0)
  const operationEnCoursRef = useRef(false)
  const lectureDepotRef = useRef(false)

  function stopPollingJob() {
    pollGenerationRef.current += 1
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
    pollTimerRef.current = null
    pollAbortRef.current?.abort()
    pollAbortRef.current = null
  }

  function memoriserSuivi(id: string) {
    if (!ID_IMPORT_VALIDE.test(id)) {
      throw new Error("Identifiant de suivi invalide. Ne relancez pas l’import avant vérification.")
    }
    try {
      if (!session.authenticated || !session.mode || !session.identifiant) throw new Error("Session indisponible")
      window.sessionStorage.setItem(CLE_SUIVI_IMPORT, JSON.stringify({
        id, mode: session.mode, identifiant: session.identifiant,
      }))
      setStockageSuiviIndisponible(false)
    } catch {
      // Le job reste suivi en mémoire et son identifiant est affiché pour une
      // reprise explicite. Un stockage bloqué ne doit pas masquer ce risque.
      setStockageSuiviIndisponible(true)
    }
  }

  function effacerSuiviMemorise() {
    try {
      window.sessionStorage.removeItem(CLE_SUIVI_IMPORT)
      setStockageSuiviIndisponible(false)
    } catch {
      setStockageSuiviIndisponible(true)
    }
  }

  useEffect(() => {
    // L'état d'un autre compte ne doit jamais être restauré dans cet onglet.
    // On le nettoie également lors d'une transition de session en cours.
    stopPollingJob()
    annulationAbortRef.current?.abort()
    operationGenerationRef.current += 1
    operationEnCoursRef.current = false
    lectureDepotRef.current = false
    setLectureDepotEnCours(false)
    annulationRef.current = false
    setAnnulationEnCours(false)
    setBusy(null)
    setError(null)
    setErreurSuivi(null)
    setResult(null)
    setActiveJob(null)
    setScan(null)
    setSourcePath("")
    setSelectedPdf(null)
    setSelectedExcel(null)
    setDropped([])
    setDragActive(false)
    if (filesInput.current) filesInput.current.value = ""
    if (folderInput.current) folderInput.current.value = ""
    setIdManuel("")
    setStockageSuiviIndisponible(false)
    try {
      const brut = window.sessionStorage.getItem(CLE_SUIVI_IMPORT)
      if (brut && session.authenticated && session.mode && session.identifiant) {
        const suivi = JSON.parse(brut)
        if (suivi?.mode === session.mode && suivi?.identifiant === session.identifiant &&
            typeof suivi?.id === "string" && ID_IMPORT_VALIDE.test(suivi.id)) {
          setActiveJob({ id: suivi.id, job_id: suivi.id, status: "pending", progress: 0, stage: "starting" })
          startPollingJob(suivi.id)
        } else {
          window.sessionStorage.removeItem(CLE_SUIVI_IMPORT)
        }
      }
    } catch {
      // Aucune reprise silencieuse possible si le stockage ou le contenu est
      // invalide. La reprise manuelle reste disponible pour l'identifiant connu.
    }
    return () => {
      operationGenerationRef.current += 1
      stopPollingJob()
      annulationAbortRef.current?.abort()
      annulationAbortRef.current = null
    }
  }, [session.authenticated, session.mode, session.identifiant])

  function effacerAnalysePrecedente() {
    setScan(null)
    setSelectedPdf(null)
    setSelectedExcel(null)
  }

  function resetAfterScan(next: ImportScanPayload) {
    stopPollingJob()
    effacerSuiviMemorise()
    setErreurSuivi(null)
    setScan(next)
    setSelectedPdf(next.candidates[0]?.path ?? null)
    setSelectedExcel(next.excel_candidates?.[0]?.path ?? null)
    setResult(null)
    setActiveJob(null)
  }

  async function callJson(url: string, method: string, body?: unknown, signal?: AbortSignal) {
    const response = await authedFetch(url, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(payload?.detail ?? "La requête a échoué.")
    return payload
  }

  function startPollingJob(jobId: string) {
    stopPollingJob()
    const generation = pollGenerationRef.current

    // Une seule requête à la fois, chacune bornée. Une réponse d'une ancienne
    // génération ne peut pas réactiver un job annulé ou un autre compte.
    async function suivre() {
      if (generation !== pollGenerationRef.current) return
      const controleur = new AbortController()
      pollAbortRef.current = controleur
      const expiration = setTimeout(() => controleur.abort(), DELAI_REQUETE_SUIVI_MS)
      try {
        const payload: ImportJobPayload = await callJson(
          `/api/imports/${encodeURIComponent(jobId)}`, "GET", undefined, controleur.signal,
        )
        if (generation !== pollGenerationRef.current) return
        const identifiants = [payload.id, payload.job_id, payload.import_id].filter((id) => id != null)
        if (identifiants.some((id) => id !== jobId) ||
            !["pending", "running", "completed", "failed", "cancelled", "needs_review",
              "upload_incomplete", "needs_confirmation"].includes(payload.status)) {
          throw new Error("Réponse de suivi incohérente")
        }
        setErreurSuivi(null)
        if (payload.status === "pending" || payload.status === "running") {
          setActiveJob({ ...payload, id: jobId })
          pollTimerRef.current = setTimeout(suivre, 500)
          return
        }

        stopPollingJob()
        setActiveJob(null)
        const avecResultat = Boolean(payload.result || payload.files_detected !== undefined || payload.publication_pending)
        if (avecResultat) {
          // Le niveau supérieur est l'état durable le plus récent ; ne pas le
          // masquer par un ancien objet result imbriqué.
          setResult({ ...payload.result, ...payload, import_id: jobId } as ImportResultPayload)
        } else {
          setResult(null)
        }
        if (payload.status === "failed") {
          setError(payload.error || "L’import a échoué.")
        } else if (payload.status === "cancelled") {
          setError(`Import annulé. Les opérations déjà commencées peuvent nécessiter une vérification.${
            payload.error ? ` ${payload.error}` : ""
          }`)
        } else if (!avecResultat) {
          setError("Résultat de l’import indisponible : vérifiez son état avant de le relancer.")
        } else {
          setError(null)
        }
        if (payload.status === "completed" || payload.status === "needs_review" || payload.status === "upload_incomplete") {
          setScan(null)
          setSelectedPdf(null)
          setSelectedExcel(null)
        }
      } catch {
        if (generation !== pollGenerationRef.current) return
        // Un échec de lecture ne dit rien sur l'état du travail. Conserver
        // l'identifiant, empêcher un nouvel import et réessayer le GET seul.
        setErreurSuivi("Suivi momentanément indisponible : nouvelle vérification automatique. L’import peut encore être en cours ; ne le relancez pas.")
        pollTimerRef.current = setTimeout(suivre, DELAI_REPRISE_SUIVI_MS)
      } finally {
        clearTimeout(expiration)
        if (pollAbortRef.current === controleur) pollAbortRef.current = null
      }
    }
    pollTimerRef.current = setTimeout(suivre, 500)
  }

  async function run(label: string, fn: (estActuel: () => boolean) => Promise<void>) {
    if (operationEnCoursRef.current) return
    operationEnCoursRef.current = true
    const generation = operationGenerationRef.current
    const estActuel = () => generation === operationGenerationRef.current
    setError(null)
    setBusy(label)
    try {
      await fn(estActuel)
    } catch (err) {
      if (estActuel()) setError(err instanceof Error ? err.message : "La requête a échoué.")
    } finally {
      if (estActuel()) {
        operationEnCoursRef.current = false
        setBusy(null)
      }
    }
  }

  const cancelActiveJob = async () => {
    if (annulationRef.current || (!activeJob?.id && !activeJob?.job_id && !activeJob?.import_id)) return
    const id = activeJob.id || activeJob.job_id || activeJob.import_id
    annulationRef.current = true
    setAnnulationEnCours(true)
    stopPollingJob()
    const generation = pollGenerationRef.current
    const controleur = new AbortController()
    annulationAbortRef.current = controleur
    const expiration = setTimeout(() => controleur.abort(), DELAI_REQUETE_SUIVI_MS)
    try {
      const reponse = await callJson(`/api/imports/${encodeURIComponent(id!)}/cancel`, "POST", undefined, controleur.signal)
      if (generation !== pollGenerationRef.current) return
      if (reponse.status !== "cancelled") throw new Error("Annulation non confirmée : vérification du statut en cours.")
      // L'accusé n'affirme pas qu'une opération externe déjà engagée s'est
      // arrêtée. Relire aussi les intentions et le résultat durable.
      setError("Annulation demandée. Vérification du statut en cours.")
    } catch (err) {
      if (generation !== pollGenerationRef.current) return
      setError(controleur.signal.aborted
        ? "Annulation non confirmée : vérification du statut en cours."
        : err instanceof Error ? err.message : "Impossible d’annuler l’import.")
    } finally {
      clearTimeout(expiration)
      if (annulationAbortRef.current === controleur) annulationAbortRef.current = null
      if (generation === pollGenerationRef.current) {
        annulationRef.current = false
        setAnnulationEnCours(false)
        startPollingJob(id!)
      }
    }
  }

  const scanFolder = () =>
    run("scan", async (estActuel) => {
      const payload = (await callJson("/api/imports/scan", "POST", { source_path: sourcePath.trim() })) as ImportScanPayload
      if (estActuel()) resetAfterScan(payload)
    })

  function suivreImport(id: string, payload?: Partial<ImportJobPayload>) {
    memoriserSuivi(id)
    setError(null)
    setErreurSuivi(null)
    setResult(null)
    setActiveJob({
      id, job_id: id, status: payload?.status || "pending",
      progress: payload?.progress || 0, stage: payload?.stage || "starting",
    })
    startPollingJob(id)
  }

  const reprendreSuivi = () => {
    const id = idManuel.trim()
    if (!ID_IMPORT_VALIDE.test(id)) {
      setError("Identifiant de suivi invalide.")
      return
    }
    suivreImport(id)
  }

  const arreterSuivi = () => {
    const id = activeJob?.id || activeJob?.job_id || activeJob?.import_id
    if (!id || !window.confirm("Arrêter le suivi n’annule pas le traitement. Il peut encore être en cours. Continuer ?")) return
    stopPollingJob()
    effacerSuiviMemorise()
    setIdManuel(id)
    setActiveJob(null)
    setErreurSuivi(null)
    setError("Suivi arrêté. Le traitement peut encore être en cours : vérifiez son état avant de lancer un nouvel import.")
  }

  const quickImport = () =>
    run("import", async (estActuel) => {
      const payload = await callJson("/api/imports", "POST", { source_path: sourcePath.trim() })
      if (!estActuel()) return
      if (payload.job_id || payload.id) {
        suivreImport(payload.job_id || payload.id, payload)
      } else {
        if (typeof payload.import_id === "string" && ID_IMPORT_VALIDE.test(payload.import_id)) memoriserSuivi(payload.import_id)
        else setStockageSuiviIndisponible(true)
        setResult(payload as ImportResultPayload)
        setScan(null)
        setSelectedPdf(null)
        setSelectedExcel(null)
      }
    })

  const confirmImport = () =>
    run("confirm", async (estActuel) => {
      if (!scan || !selectedPdf) return
      const payload = await callJson("/api/imports/confirm", "POST", {
        source_path: scan.staged_path ?? scan.source_path,
        technical_pdf: selectedPdf,
        excel_file: selectedExcel || undefined,
      })
      if (!estActuel()) return
      if (payload.job_id || payload.id) {
        suivreImport(payload.job_id || payload.id, payload)
      } else {
        if (typeof payload.import_id === "string" && ID_IMPORT_VALIDE.test(payload.import_id)) memoriserSuivi(payload.import_id)
        else setStockageSuiviIndisponible(true)
        setResult(payload as ImportResultPayload)
      }
    })

  const uploadAndScan = () =>
    run("upload", async (estActuel) => {
      if (dropped.length === 0) return
      const form = new FormData()
      const top = dropped[0].rel.includes("/") ? dropped[0].rel.split("/")[0] : "upload"
      form.append("folder", top)
      for (const item of dropped) form.append("files", item.file, item.rel)
      const response = await authedFetch("/api/imports/upload", { method: "POST", body: form })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(payload?.detail ?? "L’envoi des fichiers a échoué.")
      if (!estActuel()) return
      resetAfterScan(payload as ImportScanPayload)
      setDropped([])
    })

  const saveCorrection = (correction: ImportCorrection) =>
    run("correct", async (estActuel) => {
      if (!result) return
      const payload = (await callJson(`/api/imports/${result.import_id}`, "PATCH", correction)) as ImportResultPayload
      if (estActuel()) setResult(payload)
    })

  const retryUpload = () =>
    run("retry", async (estActuel) => {
      if (!result) return
      const payload = (await callJson(`/api/imports/${result.import_id}/retry`, "POST")) as ImportResultPayload
      if (estActuel()) setResult(payload)
    })

  // Un rapport d'analyse n'est PAS une fiche : rien n'apparaît dans les
  // dossiers tant que ce passage explicite n'a pas été fait. On l'annonce au
  // lieu de laisser croire que l'import a enregistré quelque chose.
  const creerFiche = () =>
    run("fiche", async () => {
      if (!result) return
      setCreationFiche(null)
      try {
        const payload = await callJson(`/api/imports/${encodeURIComponent(result.import_id)}/creer-fiche`, "POST")
        if (payload.statut === "traite") {
          setCreationFiche(`Fiche ${payload.fiche} créée (dossier ${payload.pieces} pièce(s) jointe(s)).`)
        } else if (payload.statut === "deja_traite") {
          setCreationFiche("Ce dossier avait déjà produit une fiche : rien n'a été dupliqué.")
        } else {
          setCreationFiche(`Fiche non créée : ${payload.raison ?? "raison inconnue"}`)
        }
      } catch (err) {
        setCreationFiche(err instanceof Error ? err.message : "La création de la fiche a échoué.")
      }
    })

  const loading = busy !== null || activeJob !== null || lectureDepotEnCours
  const candidates = scan?.candidates ?? []
  const excelCandidates = scan?.excel_candidates ?? []
  const effectiveSource = scan?.staged_path ?? scan?.source_path ?? ""

  return (
    <section className="border-b border-border bg-card/50 px-4 py-5 sm:px-6">
      <div className="mx-auto max-w-6xl">
        <div className="mb-3 flex items-baseline justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">Importer un dossier</p>
            <h2 className="mt-1 text-lg font-semibold">Analyser un dossier de référence</h2>
          </div>
          <span className="text-xs text-muted-foreground">Les fichiers d’origine ne sont jamais déplacés ni modifiés</span>
        </div>

        {/* Server folder */}
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            className="min-h-10 flex-1 rounded-md border border-input bg-background px-3 text-sm outline-none ring-primary focus:ring-2"
            placeholder="Chemin du dossier sur le serveur, ex. C:\\SEAMTECH\\Reference_001"
            value={sourcePath}
            disabled={loading}
            onChange={(event) => {
              if (loading || operationEnCoursRef.current || lectureDepotRef.current) return
              setSourcePath(event.target.value)
              effacerAnalysePrecedente()
              setDropped([])
              setError(null)
            }}
          />
          <button
            className="min-h-10 rounded-md border border-input bg-background px-5 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!sourcePath.trim() || loading}
            onClick={scanFolder}
          >
            {busy === "scan" ? "Analyse…" : "Analyser le dossier"}
          </button>
          <button
            className="min-h-10 rounded-md bg-primary px-5 text-sm font-semibold text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!sourcePath.trim() || loading}
            onClick={quickImport}
          >
            {busy === "import" || activeJob ? "Traitement…" : "Importation rapide"}
          </button>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          Le serveur doit pouvoir accéder à ce dossier. Analysez-le pour choisir le PDF technique et le fichier Excel, ou lancez l’importation rapide.
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label htmlFor="id-reprise-import" className="flex min-w-0 flex-col gap-1 text-xs text-muted-foreground">
            Identifiant d’un import à suivre
            <input
              id="id-reprise-import"
              className="min-h-9 w-72 max-w-full rounded-md border border-input bg-background px-3 font-mono text-sm text-foreground"
              value={idManuel}
              maxLength={128}
              onChange={(event) => setIdManuel(event.target.value)}
              disabled={loading}
            />
          </label>
          <button
            className="min-h-9 rounded-md border border-input bg-background px-4 text-xs font-medium disabled:opacity-50"
            disabled={loading || !ID_IMPORT_VALIDE.test(idManuel.trim())}
            onClick={reprendreSuivi}
          >
            Reprendre le suivi
          </button>
        </div>

        {/* Active Job Progress Panel */}
        {activeJob && (
          <div className="mt-4 rounded-md border border-primary/40 bg-primary/5 p-4 text-sm shadow-sm">
            <div className="flex items-center justify-between gap-4">
              <div className="flex items-center gap-2">
                <span className="relative flex h-3 w-3">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-primary"></span>
                </span>
                <span className="font-semibold text-primary">{formatStage(activeJob.stage)}</span>
              </div>
              <div className="flex items-center gap-3">
                <span className="font-mono text-xs text-muted-foreground">{activeJob.progress}%</span>
                <button
                  className="rounded border border-destructive/60 bg-background px-3 py-1 text-xs font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50"
                  disabled={annulationEnCours}
                  onClick={cancelActiveJob}
                >
                  Annuler
                </button>
              </div>
            </div>
            <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-primary transition-all duration-300 ease-out"
                style={{ width: `${Math.max(5, activeJob.progress)}%` }}
              />
            </div>
            <p className="mt-2 text-xs text-muted-foreground">Identifiant de suivi : <code>{activeJob.id || activeJob.job_id || activeJob.import_id}</code></p>
            {erreurSuivi && (
              <div className="mt-2">
                <p role="status" className="text-warning">{erreurSuivi}</p>
                <button
                  className="mt-2 rounded border border-input bg-background px-3 py-1 text-xs font-medium disabled:opacity-50"
                  disabled={annulationEnCours}
                  onClick={arreterSuivi}
                >
                  Arrêter le suivi
                </button>
              </div>
            )}
          </div>
        )}
        {stockageSuiviIndisponible && (activeJob || result) && (
          <p role="status" className="mt-2 text-xs text-warning">
            Le suivi ne peut pas être enregistré dans cet onglet. Conservez son identifiant pour le reprendre après rechargement.
          </p>
        )}

        {/* Drag & drop */}
        <div
          data-testid="drop-zone"
          aria-busy={lectureDepotEnCours}
          className={`mt-3 rounded-md border border-dashed px-4 py-5 text-center transition-colors ${
            dragActive ? "border-primary bg-primary/5" : "border-input bg-background/60"
          }`}
          onDragOver={(e) => {
            e.preventDefault()
            setDragActive(true)
          }}
          onDragLeave={() => setDragActive(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragActive(false)
            if (loading || operationEnCoursRef.current || lectureDepotRef.current) return
            const generation = operationGenerationRef.current
            lectureDepotRef.current = true
            setLectureDepotEnCours(true)
            // Une nouvelle tentative remplace l'ancienne sélection, même si
            // sa lecture échoue ensuite : ne pas envoyer une liste périmée.
            setSourcePath("")
            effacerAnalysePrecedente()
            setDropped([])
            setError(null)
            filesFromDrop(e.dataTransfer).then((items) => {
              if (generation !== operationGenerationRef.current) return
              if (items.length > 0) setDropped(items)
              else setError("Aucun fichier lisible dans ce dépôt. Aucun fichier n’a été envoyé.")
            }).catch(() => {
              if (generation === operationGenerationRef.current) setError("Lecture du dossier impossible. Aucun fichier n’a été envoyé.")
            }).finally(() => {
              if (generation === operationGenerationRef.current) {
                lectureDepotRef.current = false
                setLectureDepotEnCours(false)
              }
            })
          }}
        >
          <p className="text-sm font-medium">…ou déposez ici un dossier de référence</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Les fichiers sont copiés dans une zone temporaire pour analyse ; les originaux restent intacts.
          </p>
          {lectureDepotEnCours && <p role="status" className="mt-2 text-xs text-muted-foreground">Lecture du dossier en cours…</p>}
          <div className="mt-3 flex justify-center gap-2">
            <button
              className="min-h-9 rounded-md border border-input bg-background px-4 text-sm font-medium disabled:opacity-50"
              disabled={loading}
              onClick={() => filesInput.current?.click()}
            >
              Choisir des fichiers
            </button>
            <button
              className="min-h-9 rounded-md border border-input bg-background px-4 text-sm font-medium disabled:opacity-50"
              disabled={loading}
              onClick={() => folderInput.current?.click()}
            >
              Choisir un dossier
            </button>
          </div>
          <input
            ref={filesInput}
            type="file"
            multiple
            disabled={loading}
            className="hidden"
            onChange={(e) => {
              const list = Array.from(e.target.files ?? []).map((f) => ({
                file: f,
                rel: (f as any).webkitRelativePath || f.name,
              }))
              if (list.length > 0 && !operationEnCoursRef.current && !lectureDepotRef.current) {
                setSourcePath("")
                effacerAnalysePrecedente()
                setDropped(list)
                setError(null)
              }
              e.target.value = ""
            }}
          />
          <input
            ref={folderInput}
            type="file"
            multiple
            disabled={loading}
            className="hidden"
            {...({ webkitdirectory: "", directory: "" } as any)}
            onChange={(e) => {
              const list = Array.from(e.target.files ?? []).map((f) => ({
                file: f,
                rel: (f as any).webkitRelativePath || f.name,
              }))
              if (list.length > 0 && !operationEnCoursRef.current && !lectureDepotRef.current) {
                setSourcePath("")
                effacerAnalysePrecedente()
                setDropped(list)
                setError(null)
              }
              e.target.value = ""
            }}
          />
          {dropped.length > 0 && (
            <div className="mx-auto mt-3 max-w-2xl rounded-md border border-border bg-card p-3 text-left">
              <p className="text-xs font-semibold text-muted-foreground">
                {dropped.length} fichier{dropped.length === 1 ? "" : "s"} prêts à envoyer
              </p>
              <ul className="mt-1 max-h-28 overflow-auto text-xs">
                {dropped.slice(0, 50).map((item) => (
                  <li key={item.rel} className="flex justify-between gap-3 py-0.5">
                    <span className="truncate">{item.rel}</span>
                    <span className="shrink-0 text-muted-foreground">{formatBytes(item.file.size)}</span>
                  </li>
                ))}
                {dropped.length > 50 && <li className="text-muted-foreground">…et {dropped.length - 50} de plus</li>}
              </ul>
              <div className="mt-2 flex justify-end gap-2">
                <button
                  className="min-h-8 rounded-md border border-input bg-background px-3 text-xs font-medium"
                  disabled={loading}
                  onClick={() => setDropped([])}
                >
                  Effacer
                </button>
                <button
                  className="min-h-8 rounded-md bg-primary px-4 text-xs font-semibold text-primary-foreground disabled:opacity-50"
                  disabled={loading}
                  onClick={uploadAndScan}
                >
                  {busy === "upload" ? "Envoi…" : "Envoyer et analyser"}
                </button>
              </div>
            </div>
          )}
        </div>

        {error && <p className="mt-3 text-sm text-destructive">{error}</p>}

        {/* Scan Candidates */}
        {scan && (
          <div className="mt-4 space-y-4 rounded-md border border-border bg-background p-4 text-sm">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <p className="font-medium">
                Fichiers détectés dans <span className="font-mono text-xs">{effectiveSource}</span>
              </p>
              <span className="text-xs text-muted-foreground">{scan.files_detected} fichiers analysés</span>
            </div>
            {scan.warnings?.length > 0 && <p className="text-warning">{scan.warnings.join(" ")}</p>}

            {/* Technical PDFs */}
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
                1. Choisir le PDF technique ({candidates.length} détecté(s))
              </p>
              {candidates.length > 0 ? (
                <ul className="space-y-2">
                  {candidates.map((candidate) => (
                    <li key={candidate.path}>
                      <label
                        className={`flex cursor-pointer items-start gap-3 rounded-md border p-3 ${
                          selectedPdf === candidate.path ? "border-primary bg-primary/5" : "border-border"
                        }`}
                      >
                        <input
                          type="radio"
                          name="technical-pdf"
                          className="mt-1"
                          disabled={loading}
                          checked={selectedPdf === candidate.path}
                          onChange={() => setSelectedPdf(candidate.path)}
                        />
                        <span className="min-w-0">
                          <span className="block truncate font-medium">{candidate.name}</span>
                          <span className="block truncate font-mono text-xs text-muted-foreground">{candidate.path}</span>
                          <span className="mt-1 block text-xs text-muted-foreground">
                            {candidate.anchor_count} ancres : {candidate.anchors_matched.join(", ")}
                          </span>
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-muted-foreground">Aucun PDF technique n’a été trouvé dans ce dossier.</p>
              )}
            </div>

            {/* Excel Files */}
            {excelCandidates.length > 0 && (
              <div>
                <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
                  2. Choisir le fichier Excel / nomenclature (facultatif, {excelCandidates.length} détecté(s))
                </p>
                <ul className="space-y-2">
                  <li>
                    <label
                      className={`flex cursor-pointer items-start gap-3 rounded-md border p-2.5 ${
                        selectedExcel === null ? "border-primary bg-primary/5" : "border-border"
                      }`}
                    >
                      <input
                        type="radio"
                        name="excel-file"
                        className="mt-0.5"
                        disabled={loading}
                        checked={selectedExcel === null}
                        onChange={() => setSelectedExcel(null)}
                      />
                      <span className="text-xs font-medium text-muted-foreground">Aucun (ne pas importer de fichier Excel)</span>
                    </label>
                  </li>
                  {excelCandidates.map((candidate) => (
                    <li key={candidate.path}>
                      <label
                        className={`flex cursor-pointer items-start gap-3 rounded-md border p-3 ${
                          selectedExcel === candidate.path ? "border-primary bg-primary/5" : "border-border"
                        }`}
                      >
                        <input
                          type="radio"
                          name="excel-file"
                          className="mt-1"
                          disabled={loading}
                          checked={selectedExcel === candidate.path}
                          onChange={() => setSelectedExcel(candidate.path)}
                        />
                        <span className="min-w-0">
                          <span className="block truncate font-medium">{candidate.name}</span>
                          <span className="block truncate font-mono text-xs text-muted-foreground">{candidate.path}</span>
                          <span className="mt-1 block text-xs text-muted-foreground">
                            Classeur Excel ({formatBytes(candidate.size)})
                          </span>
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="flex justify-end pt-2">
              <button
                className="min-h-10 rounded-md bg-primary px-5 text-sm font-semibold text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50"
                disabled={!selectedPdf || loading}
                onClick={confirmImport}
              >
                {busy === "confirm" || activeJob ? "Traitement…" : "Importer les fichiers sélectionnés"}
              </button>
            </div>
          </div>
        )}

        {/* Result */}
        {result && (
          <div className="mt-4 rounded-md border border-border bg-background p-4 text-sm">
            <p className="mb-2 text-xs text-muted-foreground">Identifiant de suivi : <code>{result.import_id}</code></p>
            <div className="flex flex-wrap gap-x-6 gap-y-2">
              <span>
                État : <strong>{result.status}</strong>
              </span>
              <span>
                Fichiers : <strong>{result.files_detected}</strong>
              </span>
              <span>
                PDF techniques : <strong>{result.analyzed_files}</strong>
              </span>
              <span>
                Envoi : <strong>{result.upload_status}</strong>
              </span>
            </div>
            {/* Boutons de téléchargement des rapports et fichiers source. */}
            <div className="mt-3 flex flex-wrap gap-2">
              {result.report_path && peutRapport && (
                <a
                  href={`/api/imports/${encodeURIComponent(result.import_id)}/artifacts/report_pdf`}
                  className="inline-flex min-h-8 items-center rounded-md bg-primary px-3 text-xs font-semibold text-primary-foreground hover:bg-primary/90"
                  download
                >
                  Télécharger le rapport PDF
                </a>
              )}
              {result.report_docx_path && peutRapport && (
                <a
                  href={`/api/imports/${encodeURIComponent(result.import_id)}/artifacts/report_docx`}
                  className="inline-flex min-h-8 items-center rounded-md border border-input bg-background px-3 text-xs font-medium hover:bg-muted"
                  download
                >
                  Télécharger le rapport Word
                </a>
              )}
              {result.technical_pdf && peutSource && (
                <a
                  href={`/api/imports/${encodeURIComponent(result.import_id)}/artifacts/source_pdf`}
                  className="inline-flex min-h-8 items-center rounded-md border border-input bg-background px-3 text-xs font-medium hover:bg-muted"
                  download
                >
                  Télécharger le PDF source
                </a>
              )}
              {result.excel_file && peutSource && (
                <a
                  href={`/api/imports/${encodeURIComponent(result.import_id)}/artifacts/source_excel`}
                  className="inline-flex min-h-8 items-center rounded-md border border-input bg-background px-3 text-xs font-medium hover:bg-muted"
                  download
                >
                  Télécharger le fichier Excel
                </a>
              )}
              {result.technical_pdf && peutSource && (
                <button
                  className="inline-flex min-h-8 items-center rounded-md border border-primary bg-primary/10 px-3 text-xs font-semibold text-primary hover:bg-primary/20 disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={loading}
                  onClick={creerFiche}
                >
                  {busy === "fiche" ? "Création…" : "Créer la fiche dans la base"}
                </button>
              )}
            </div>
            {result.technical_pdf && (
              <p className="mt-2 text-xs text-muted-foreground">
                Un rapport d’analyse n’enregistre aucune fiche : utilisez « Créer la fiche dans la base » pour la
                ranger dans les dossiers (ou déposez directement le dossier).
              </p>
            )}
            {creationFiche && (
              <p className="mt-2 rounded-md border border-primary/40 bg-primary/5 px-3 py-2 text-xs font-medium text-primary" role="status">
                {creationFiche}
              </p>
            )}
            {result.technical_pdf && (
              <p className="mt-2 truncate font-mono text-xs text-muted-foreground">PDF: {result.technical_pdf}</p>
            )}
            {result.excel_file && (
              <p className="mt-1 truncate font-mono text-xs text-muted-foreground">Excel: {result.excel_file}</p>
            )}
            {result.warnings?.length > 0 && <p className="mt-3 text-warning">{result.warnings.join(" ")}</p>}
            
            {result.publication_pending && (
              <div role="status" className="mt-3 rounded-md border border-warning/40 bg-warning/10 p-3 text-xs">
                <strong>Publication incertaine :</strong> une réconciliation est nécessaire avant toute nouvelle tentative.
                Conservez les fichiers source ; ne relancez pas l’envoi à l’aveugle.
              </div>
            )}
            {result.report_generation_pending && (
              <div role="status" className="mt-3 rounded-md border border-warning/40 bg-warning/10 p-3 text-xs">
                La régénération des rapports est interrompue.{" "}
                {result.publication_pending
                  ? "La publication doit d’abord être réconciliée."
                  : "Une correction autorisée peut reprendre la génération ; ne relancez pas encore l’envoi."}
              </div>
            )}
            {(result.upload_status === "upload_incomplete" || result.status === "upload_incomplete") && (
              <div className="mt-3 rounded-md border border-warning/40 bg-warning/10 p-3 text-xs">
                <strong>Envoi incomplet :</strong> certains fichiers n’ont pas de copie durable prouvée.{" "}
                {result.quarantine?.erreur
                  ? "L’emplacement de la source n’a pas pu être confirmé ; vérifiez-le avant toute opération."
                  : result.quarantine?.deplace === true
                    ? "Le dossier a été déplacé vers la quarantaine. Vérifiez son emplacement avant toute opération."
                    : result.quarantine?.deplace === false
                      ? "Aucun déplacement n’a été effectué : vérifiez que le dossier est conservé sur place."
                      : "L’emplacement de la source n’a pas été confirmé ; vérifiez-le avant toute opération."}
                {result.cleanup?.purge === false && " Aucune purge automatique n’a été réalisée pour cet import."}
              </div>
            )}

            {/* Métadonnées extraites du PDF technique. */}
            {result.data && (
              <div className="mt-3 overflow-x-auto">
                <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-1">
                  Données extraites de la fiche technique
                </p>
                <table className="w-full text-left text-xs">
                  <tbody>
                    {(
                      [
                        ["Référence", result.data.reference],
                        ["Matériau", result.data.material],
                        [
                          "Dimensions",
                          result.data.dimensions
                            ? [result.data.dimensions.length, result.data.dimensions.width, result.data.dimensions.height]
                                .filter((v) => v !== null && v !== undefined)
                                .join(" × ") + (result.data.dimensions.unit ? ` ${result.data.dimensions.unit}` : "")
                            : null,
                        ],
                        [
                          "Valeurs normalisées",
                          result.data.dimensions?.length_mm != null
                            ? `${result.data.dimensions.length_mm} × ${result.data.dimensions.width_mm} mm`
                            : null,
                        ],
                        ["Quantité", result.data.quantity],
                        ["Description", result.data.description],
                        ["Extraction", `${result.data.extraction_status} (${Math.round(result.data.confidence * 100)}%)`],
                      ] as const
                    ).map(([label, value]) => (
                      <tr key={label} className="border-t border-border/60">
                        <th className="py-1.5 pr-4 font-semibold text-muted-foreground">{label}</th>
                        <td className="py-1.5">{value ?? <span className="text-muted-foreground">À vérifier</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* Résumé du classeur Excel. */}
            {result.excel_summary && (
              <div className="mt-4 rounded border border-border/80 bg-muted/20 p-3">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Résumé du classeur Excel ({result.excel_summary.filename})
                  </p>
                  <span className="text-xs font-medium text-muted-foreground">
                    {result.excel_summary.total_sheets} feuille{result.excel_summary.total_sheets === 1 ? "" : "s"}
                  </span>
                </div>
                {result.excel_summary.sheets.map((sheet) => (
                  <div key={sheet.sheet_name} className="mt-2">
                    <p className="text-xs font-medium text-foreground">
                      Feuille : <span className="font-semibold">{sheet.sheet_name}</span> ({sheet.row_count} lignes × {sheet.column_count} colonnes)
                    </p>
                    {sheet.sample_rows && sheet.sample_rows.length > 0 && (
                      <div className="mt-1 overflow-x-auto">
                        <table className="w-full text-left text-xs border border-border/40">
                          <thead className="bg-muted/60">
                            <tr>
                              {sheet.headers.map((h, i) => (
                                <th key={i} className="p-1.5 font-semibold text-muted-foreground border-b border-border/40">
                                  {h}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {sheet.sample_rows.map((row, rIdx) => (
                              <tr key={rIdx} className="border-t border-border/20 hover:bg-muted/30">
                                {sheet.headers.map((_header, cIdx) => (
                                  <td key={cIdx} className="p-1.5 font-mono text-[11px]">
                                    {row[cIdx] || "—"}
                                  </td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {(result.upload_status === "pending_retry" || result.upload_status === "upload_incomplete") &&
              result.publication_pending === false && !result.report_generation_pending && (
              <div className="mt-3 flex justify-end">
                <button
                  className="min-h-9 rounded-md border border-input bg-background px-4 text-sm font-medium disabled:opacity-50"
                  disabled={loading}
                  onClick={retryUpload}
                >
                  {busy === "retry" ? "Nouvel essai…" : "Relancer l’envoi"}
                </button>
              </div>
            )}
            {result.data && peutSource && !result.publication_pending && (
              <CorrectionForm data={result.data} saving={busy === "correct"} onSave={saveCorrection} />
            )}
          </div>
        )}
      </div>
    </section>
  )
}


export function ImportPanel() {
  const { role, chargement } = useSession()
  if (chargement) return <section className="border-b border-border px-4 py-5 text-sm text-muted-foreground">Vérification de votre rôle…</section>
  if (!peutDeposer(role)) {
    return <AccesRefuse message="L’import de dossiers est réservé aux rôles admin, utilisateur et approbateur. Le rôle lecture peut rechercher et prévisualiser les documents existants." />
  }
  return <ImportPanelInterne />
}
