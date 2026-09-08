"use client"

import { useRef, useState } from "react"
import type { ImportCorrection, ImportResultPayload, ImportScanPayload } from "@/lib/types"
import { CorrectionForm } from "@/components/correction-form"

interface DroppedFile {
  file: File
  rel: string
}

async function readEntry(entry: any, base: string, out: DroppedFile[]): Promise<void> {
  if (entry.isFile) {
    const file: File = await new Promise((resolve, reject) => entry.file(resolve, reject))
    out.push({ file, rel: base + file.name })
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
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

export function ImportPanel() {
  const [sourcePath, setSourcePath] = useState("")
  const [scan, setScan] = useState<ImportScanPayload | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [result, setResult] = useState<ImportResultPayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const [dropped, setDropped] = useState<DroppedFile[]>([])
  const filesInput = useRef<HTMLInputElement>(null)
  const folderInput = useRef<HTMLInputElement>(null)

  function resetAfterScan(next: ImportScanPayload) {
    setScan(next)
    setSelected(next.candidates[0]?.path ?? null)
    setResult(null)
  }

  async function callJson(url: string, method: string, body?: unknown) {
    const response = await fetch(url, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(payload?.detail ?? "Request failed.")
    return payload
  }

  async function run(label: string, fn: () => Promise<void>) {
    setError(null)
    setBusy(label)
    try {
      await fn()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed.")
    } finally {
      setBusy(null)
    }
  }

  const scanFolder = () =>
    run("scan", async () => {
      const payload = (await callJson("/api/imports/scan", "POST", { source_path: sourcePath.trim() })) as ImportScanPayload
      resetAfterScan(payload)
    })

  const quickImport = () =>
    run("import", async () => {
      const payload = (await callJson("/api/imports", "POST", { source_path: sourcePath.trim() })) as ImportResultPayload
      setResult(payload)
      setScan(null)
      setSelected(null)
    })

  const confirmImport = () =>
    run("confirm", async () => {
      if (!scan || !selected) return
      const payload = (await callJson("/api/imports/confirm", "POST", {
        source_path: scan.staged_path ?? scan.source_path,
        technical_pdf: selected,
      })) as ImportResultPayload
      setResult(payload)
    })

  const uploadAndScan = () =>
    run("upload", async () => {
      if (dropped.length === 0) return
      const form = new FormData()
      const top = dropped[0].rel.includes("/") ? dropped[0].rel.split("/")[0] : "upload"
      form.append("folder", top)
      for (const item of dropped) form.append("files", item.file, item.rel)
      const response = await fetch("/api/imports/upload", { method: "POST", body: form })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(payload?.detail ?? "Upload failed.")
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

  const loading = busy !== null
  const candidates = scan?.candidates ?? []
  const effectiveSource = scan?.staged_path ?? scan?.source_path ?? ""

  return (
    <section className="border-b border-border bg-card/50 px-4 py-5 sm:px-6">
      <div className="mx-auto max-w-6xl">
        <div className="mb-3 flex items-baseline justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">Import Reference</p>
            <h2 className="mt-1 text-lg font-semibold">Analyze a reference folder</h2>
          </div>
          <span className="text-xs text-muted-foreground">Original files are never moved or modified</span>
        </div>

        {/* Server folder */}
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            className="min-h-10 flex-1 rounded-md border border-input bg-background px-3 text-sm outline-none ring-primary focus:ring-2"
            placeholder="Server folder path, e.g. C:\\SEAMTECH\\Reference_001"
            value={sourcePath}
            onChange={(event) => setSourcePath(event.target.value)}
          />
          <button
            className="min-h-10 rounded-md border border-input bg-background px-5 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!sourcePath.trim() || loading}
            onClick={scanFolder}
          >
            {busy === "scan" ? "Scanning…" : "Scan folder"}
          </button>
          <button
            className="min-h-10 rounded-md bg-primary px-5 text-sm font-semibold text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!sourcePath.trim() || loading}
            onClick={quickImport}
          >
            {busy === "import" ? "Processing…" : "Quick import"}
          </button>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          The server must have access to this folder. Scan first to choose between technical PDFs, or quick-import to
          take the first match.
        </p>

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
          <p className="text-sm font-medium">…or drag &amp; drop a reference folder here</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Files are staged on the server for analysis only — your originals stay untouched.
          </p>
          <div className="mt-3 flex justify-center gap-2">
            <button
              className="min-h-9 rounded-md border border-input bg-background px-4 text-sm font-medium disabled:opacity-50"
              disabled={loading}
              onClick={() => filesInput.current?.click()}
            >
              Browse files
            </button>
            <button
              className="min-h-9 rounded-md border border-input bg-background px-4 text-sm font-medium disabled:opacity-50"
              disabled={loading}
              onClick={() => folderInput.current?.click()}
            >
              Browse folder
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
                {dropped.length} file{dropped.length === 1 ? "" : "s"} ready to stage
              </p>
              <ul className="mt-1 max-h-28 overflow-auto text-xs">
                {dropped.slice(0, 50).map((item) => (
                  <li key={item.rel} className="flex justify-between gap-3 py-0.5">
                    <span className="truncate">{item.rel}</span>
                    <span className="shrink-0 text-muted-foreground">{formatBytes(item.file.size)}</span>
                  </li>
                ))}
                {dropped.length > 50 && <li className="text-muted-foreground">…and {dropped.length - 50} more</li>}
              </ul>
              <div className="mt-2 flex justify-end gap-2">
                <button
                  className="min-h-8 rounded-md border border-input bg-background px-3 text-xs font-medium"
                  disabled={loading}
                  onClick={() => setDropped([])}
                >
                  Clear
                </button>
                <button
                  className="min-h-8 rounded-md bg-primary px-4 text-xs font-semibold text-primary-foreground disabled:opacity-50"
                  disabled={loading}
                  onClick={uploadAndScan}
                >
                  {busy === "upload" ? "Uploading…" : "Upload & scan"}
                </button>
              </div>
            </div>
          )}
        </div>

        {error && <p className="mt-3 text-sm text-destructive">{error}</p>}

        {/* Candidates */}
        {scan && (
          <div className="mt-4 rounded-md border border-border bg-background p-4 text-sm">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <p className="font-medium">
                {candidates.length} technical PDF{candidates.length === 1 ? "" : "s"} in{" "}
                <span className="font-mono text-xs">{effectiveSource}</span>
              </p>
              <span className="text-xs text-muted-foreground">{scan.files_detected} files scanned</span>
            </div>
            {scan.warnings?.length > 0 && <p className="mt-2 text-warning">{scan.warnings.join(" ")}</p>}
            {candidates.length > 0 ? (
              <>
                <ul className="mt-3 space-y-2">
                  {candidates.map((candidate) => (
                    <li key={candidate.path}>
                      <label
                        className={`flex cursor-pointer items-start gap-3 rounded-md border p-3 ${
                          selected === candidate.path ? "border-primary bg-primary/5" : "border-border"
                        }`}
                      >
                        <input
                          type="radio"
                          name="technical-pdf"
                          className="mt-1"
                          checked={selected === candidate.path}
                          onChange={() => setSelected(candidate.path)}
                        />
                        <span className="min-w-0">
                          <span className="block truncate font-medium">{candidate.name}</span>
                          <span className="block truncate font-mono text-xs text-muted-foreground">{candidate.path}</span>
                          <span className="mt-1 block text-xs text-muted-foreground">
                            {candidate.anchor_count} anchors: {candidate.anchors_matched.join(", ")}
                          </span>
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
                <div className="mt-3 flex justify-end">
                  <button
                    className="min-h-10 rounded-md bg-primary px-5 text-sm font-semibold text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50"
                    disabled={!selected || loading}
                    onClick={confirmImport}
                  >
                    {busy === "confirm" ? "Processing…" : "Import selected PDF"}
                  </button>
                </div>
              </>
            ) : (
              <p className="mt-2 text-sm text-muted-foreground">No technical PDF found in this folder.</p>
            )}
          </div>
        )}

        {/* Result */}
        {result && (
          <div className="mt-4 rounded-md border border-border bg-background p-4 text-sm">
            <div className="flex flex-wrap gap-x-6 gap-y-2">
              <span>
                Status: <strong>{result.status}</strong>
              </span>
              <span>
                Files: <strong>{result.files_detected}</strong>
              </span>
              <span>
                Technical PDFs: <strong>{result.analyzed_files}</strong>
              </span>
              <span>
                Upload: <strong>{result.upload_status}</strong>
              </span>
              {result.report_docx_path && (
                <span className="text-muted-foreground">
                  Reports: <span className="font-mono text-xs">PDF + Word</span>
                </span>
              )}
            </div>
            {result.technical_pdf && (
              <p className="mt-2 truncate font-mono text-xs text-muted-foreground">{result.technical_pdf}</p>
            )}
            {result.warnings?.length > 0 && <p className="mt-3 text-warning">{result.warnings.join(" ")}</p>}
            {result.data && (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <tbody>
                    {(
                      [
                        ["Reference", result.data.reference],
                        ["Material", result.data.material],
                        [
                          "Dimensions",
                          result.data.dimensions
                            ? [result.data.dimensions.length, result.data.dimensions.width, result.data.dimensions.height]
                                .filter((v) => v !== null && v !== undefined)
                                .join(" × ") + (result.data.dimensions.unit ? ` ${result.data.dimensions.unit}` : "")
                            : null,
                        ],
                        [
                          "Normalized",
                          result.data.dimensions?.length_mm != null
                            ? `${result.data.dimensions.length_mm} × ${result.data.dimensions.width_mm} mm`
                            : null,
                        ],
                        ["Quantity", result.data.quantity],
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
            {result.upload_status === "pending_retry" && (
              <div className="mt-3 flex justify-end">
                <button
                  className="min-h-9 rounded-md border border-input bg-background px-4 text-sm font-medium disabled:opacity-50"
                  disabled={loading}
                  onClick={retryUpload}
                >
                  {busy === "retry" ? "Retrying…" : "Retry OneDrive upload"}
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
