"use client"

import { useRef, useState, useEffect } from "react"
import type { ImportCorrection, ImportJobPayload, ImportResultPayload, ImportScanPayload } from "@/lib/types"
import { CorrectionForm } from "@/components/correction-form"
import { authedFetch } from "@/lib/authed-fetch"

interface DroppedFile {
  file: File
  rel: string
}

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
  }
}

async function filesFromDrop(dt: DataTransfer): Promise<DroppedFile[]> {
  const items = Array.from(dt.items ?? [])
  const entries = items.map((i: any) => i.webkitGetAsEntry?.()).filter(Boolean)
  if (entries.length > 0) {
    const out: DroppedFile[] = []
    for (const entry of entries) await readEntry(entry, "", out)
    if (out.length > 0) return out
  }
  return Array.from(dt.files ?? []).map((f) => ({ file: f, rel: (f as any).webkitRelativePath || f.name }))
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

export function ImportPanel() {
  const [sourcePath, setSourcePath] = useState("")
  const [scan, setScan] = useState<ImportScanPayload | null>(null)
  const [selectedPdf, setSelectedPdf] = useState<string | null>(null)
  const [selectedExcel, setSelectedExcel] = useState<string | null>(null)
  const [result, setResult] = useState<ImportResultPayload | null>(null)
  const [activeJob, setActiveJob] = useState<ImportJobPayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const [dropped, setDropped] = useState<DroppedFile[]>([])
  const filesInput = useRef<HTMLInputElement>(null)
  const folderInput = useRef<HTMLInputElement>(null)
  const pollTimerRef = useRef<NodeJS.Timeout | null>(null)

  useEffect(() => {
    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current)
      }
    }
  }, [])

  function resetAfterScan(next: ImportScanPayload) {
    setScan(next)
    setSelectedPdf(next.candidates[0]?.path ?? null)
    setSelectedExcel(next.excel_candidates?.[0]?.path ?? null)
    setResult(null)
    setActiveJob(null)
  }

  async function callJson(url: string, method: string, body?: unknown) {
    const response = await authedFetch(url, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(payload?.detail ?? "La requête a échoué.")
    return payload
  }

  function startPollingJob(jobId: string) {
    if (pollTimerRef.current) clearInterval(pollTimerRef.current)

    pollTimerRef.current = setInterval(async () => {
      try {
        const payload: ImportJobPayload = await callJson(`/api/imports/${encodeURIComponent(jobId)}`, "GET")
        setActiveJob(payload)

        if (payload.status === "completed") {
          if (pollTimerRef.current) clearInterval(pollTimerRef.current)
          setResult(payload.result || (payload as unknown as ImportResultPayload))
          setActiveJob(null)
          setScan(null)
          setSelectedPdf(null)
          setSelectedExcel(null)
        } else if (payload.status === "failed") {
          if (pollTimerRef.current) clearInterval(pollTimerRef.current)
          setError(payload.error || "L’import a échoué.")
          setActiveJob(null)
        } else if (payload.status === "cancelled") {
          if (pollTimerRef.current) clearInterval(pollTimerRef.current)
          setActiveJob(null)
          setError("L’import a été annulé.")
        }
      } catch (err) {
        if (pollTimerRef.current) clearInterval(pollTimerRef.current)
        setError(err instanceof Error ? err.message : "Erreur lors du suivi de l’import.")
        setActiveJob(null)
      }
    }, 500)
  }

  async function run(label: string, fn: () => Promise<void>) {
    setError(null)
    setBusy(label)
    try {
      await fn()
    } catch (err) {
      setError(err instanceof Error ? err.message : "La requête a échoué.")
    } finally {
      setBusy(null)
    }
  }

  const cancelActiveJob = async () => {
    if (!activeJob?.id && !activeJob?.job_id && !activeJob?.import_id) return
    const id = activeJob.id || activeJob.job_id || activeJob.import_id
    try {
      await callJson(`/api/imports/${encodeURIComponent(id!)}/cancel`, "POST")
      if (pollTimerRef.current) clearInterval(pollTimerRef.current)
      setActiveJob(null)
      setError("Import annulé.")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Impossible d’annuler l’import.")
    }
  }

  const scanFolder = () =>
    run("scan", async () => {
      const payload = (await callJson("/api/imports/scan", "POST", { source_path: sourcePath.trim() })) as ImportScanPayload
      resetAfterScan(payload)
    })

  const quickImport = () =>
    run("import", async () => {
      const payload = await callJson("/api/imports", "POST", { source_path: sourcePath.trim() })
      if (payload.job_id || payload.id) {
        const jobId = payload.job_id || payload.id
        setActiveJob({
          id: jobId,
          status: payload.status || "pending",
          progress: payload.progress || 0,
          stage: payload.stage || "queued",
          source_path: sourcePath.trim(),
        })
        startPollingJob(jobId)
      } else {
        setResult(payload as ImportResultPayload)
        setScan(null)
        setSelectedPdf(null)
        setSelectedExcel(null)
      }
    })

  const confirmImport = () =>
    run("confirm", async () => {
      if (!scan || !selectedPdf) return
      const payload = await callJson("/api/imports/confirm", "POST", {
        source_path: scan.staged_path ?? scan.source_path,
        technical_pdf: selectedPdf,
        excel_file: selectedExcel || undefined,
      })
      if (payload.job_id || payload.id) {
        const jobId = payload.job_id || payload.id
        setActiveJob({
          id: jobId,
          status: payload.status || "pending",
          progress: payload.progress || 0,
          stage: payload.stage || "queued",
        })
        startPollingJob(jobId)
      } else {
        setResult(payload as ImportResultPayload)
      }
    })

  const uploadAndScan = () =>
    run("upload", async () => {
      if (dropped.length === 0) return
      const form = new FormData()
      const top = dropped[0].rel.includes("/") ? dropped[0].rel.split("/")[0] : "upload"
      form.append("folder", top)
      for (const item of dropped) form.append("files", item.file, item.rel)
      const response = await authedFetch("/api/imports/upload", { method: "POST", body: form })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(payload?.detail ?? "L’envoi des fichiers a échoué.")
      resetAfterScan(payload as ImportScanPayload)
      setDropped([])
    })

  const saveCorrection = (correction: ImportCorrection) =>
    run("correct", async () => {
      if (!result) return
      const payload = (await callJson(`/api/imports/${result.import_id}`, "PATCH", correction)) as ImportResultPayload
      setResult(payload)
    })

  const retryUpload = () =>
    run("retry", async () => {
      if (!result) return
      const payload = (await callJson(`/api/imports/${result.import_id}/retry`, "POST")) as ImportResultPayload
      setResult(payload)
    })

  const loading = busy !== null || activeJob !== null
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
            onChange={(event) => setSourcePath(event.target.value)}
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
                  className="rounded border border-destructive/60 bg-background px-3 py-1 text-xs font-medium text-destructive hover:bg-destructive/10"
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
          </div>
        )}

        {/* Drag & drop */}
        <div
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
            filesFromDrop(e.dataTransfer).then((items) => {
              if (items.length > 0) {
                setDropped(items)
                setError(null)
              }
            })
          }}
        >
          <p className="text-sm font-medium">…ou déposez ici un dossier de référence</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Les fichiers sont copiés dans une zone temporaire pour analyse ; les originaux restent intacts.
          </p>
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
            className="hidden"
            onChange={(e) => {
              const list = Array.from(e.target.files ?? []).map((f) => ({
                file: f,
                rel: (f as any).webkitRelativePath || f.name,
              }))
              if (list.length > 0) setDropped(list)
              e.target.value = ""
            }}
          />
          <input
            ref={folderInput}
            type="file"
            multiple
            className="hidden"
            {...({ webkitdirectory: "", directory: "" } as any)}
            onChange={(e) => {
              const list = Array.from(e.target.files ?? []).map((f) => ({
                file: f,
                rel: (f as any).webkitRelativePath || f.name,
              }))
              if (list.length > 0) setDropped(list)
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
              {result.report_path && (
                <a
                  href={`/api/imports/${encodeURIComponent(result.import_id)}/artifacts/report_pdf`}
                  className="inline-flex min-h-8 items-center rounded-md bg-primary px-3 text-xs font-semibold text-primary-foreground hover:bg-primary/90"
                  download
                >
                  Télécharger le rapport PDF
                </a>
              )}
              {result.report_docx_path && (
                <a
                  href={`/api/imports/${encodeURIComponent(result.import_id)}/artifacts/report_docx`}
                  className="inline-flex min-h-8 items-center rounded-md border border-input bg-background px-3 text-xs font-medium hover:bg-muted"
                  download
                >
                  Télécharger le rapport Word
                </a>
              )}
              {result.technical_pdf && (
                <a
                  href={`/api/imports/${encodeURIComponent(result.import_id)}/artifacts/source_pdf`}
                  className="inline-flex min-h-8 items-center rounded-md border border-input bg-background px-3 text-xs font-medium hover:bg-muted"
                  download
                >
                  Télécharger le PDF source
                </a>
              )}
              {result.excel_file && (
                <a
                  href={`/api/imports/${encodeURIComponent(result.import_id)}/artifacts/source_excel`}
                  className="inline-flex min-h-8 items-center rounded-md border border-input bg-background px-3 text-xs font-medium hover:bg-muted"
                  download
                >
                  Télécharger le fichier Excel
                </a>
              )}
            </div>
            {result.technical_pdf && (
              <p className="mt-2 truncate font-mono text-xs text-muted-foreground">PDF: {result.technical_pdf}</p>
            )}
            {result.excel_file && (
              <p className="mt-1 truncate font-mono text-xs text-muted-foreground">Excel: {result.excel_file}</p>
            )}
            {result.warnings?.length > 0 && <p className="mt-3 text-warning">{result.warnings.join(" ")}</p>}
            
            {/* Envoi incomplet : les originaux sont conservés en quarantaine. */}
            {(result.upload_status === "upload_incomplete" || result.status === "upload_incomplete") && (
              <div className="mt-3 rounded-md border border-warning/40 bg-warning/10 p-3 text-xs">
                <strong>Envoi incomplet :</strong> Certains fichiers n’ont pas pu être envoyés vers le stockage documentaire. Le dossier source a été placé en quarantaine et ne sera pas supprimé. Vous pouvez relancer l’envoi.
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
                    Résumé du classeur Excel ({result.excel_summary.name})
                  </p>
                  <span className="text-xs font-medium text-muted-foreground">
                    {result.excel_summary.total_sheets} feuille{result.excel_summary.total_sheets === 1 ? "" : "s"}
                  </span>
                </div>
                {result.excel_summary.sheets.map((sheet) => (
                  <div key={sheet.name} className="mt-2">
                    <p className="text-xs font-medium text-foreground">
                      Feuille : <span className="font-semibold">{sheet.name}</span> ({sheet.max_row} lignes × {sheet.max_column} colonnes)
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
                                {sheet.headers.map((h, cIdx) => (
                                  <td key={cIdx} className="p-1.5 font-mono text-[11px]">
                                    {row[h] || "—"}
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

            {(result.upload_status === "pending_retry" || result.upload_status === "upload_incomplete") && (
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
            {result.data && (
              <CorrectionForm data={result.data} saving={busy === "correct"} onSave={saveCorrection} />
            )}
          </div>
        )}
      </div>
    </section>
  )
}
