"use client"

import { useState } from "react"

export function ImportPanel() {
  const [sourcePath, setSourcePath] = useState("")
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function startImport() {
    setError(null)
    setResult(null)
    setLoading(true)
    try {
      const response = await fetch("/api/imports", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_path: sourcePath }),
      })
      const body = await response.json()
      if (!response.ok) throw new Error(body.detail ?? "Import failed.")
      setResult(body)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed.")
    } finally {
      setLoading(false)
    }
  }

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
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            className="min-h-10 flex-1 rounded-md border border-input bg-background px-3 text-sm outline-none ring-primary focus:ring-2"
            placeholder="Folder path, e.g. C:\\SEAMTECH\\Reference_001"
            value={sourcePath}
            onChange={(event) => setSourcePath(event.target.value)}
          />
          <button
            className="min-h-10 rounded-md bg-primary px-5 text-sm font-semibold text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!sourcePath.trim() || loading}
            onClick={startImport}
          >
            {loading ? "Processing…" : "Start import"}
          </button>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">The server must have access to this folder. PDFs containing sail manufacturing information are analyzed; plans and other files are stored only.</p>
        {error && <p className="mt-3 text-sm text-destructive">{error}</p>}
        {result && (
          <div className="mt-4 rounded-md border border-border bg-background p-4 text-sm">
            <div className="flex flex-wrap gap-x-6 gap-y-2">
              <span>Status: <strong>{result.status}</strong></span>
              <span>Files: <strong>{result.files_detected}</strong></span>
              <span>Technical PDFs: <strong>{result.analyzed_files}</strong></span>
              <span>Upload: <strong>{result.upload_status}</strong></span>
            </div>
            {result.warnings?.length > 0 && <p className="mt-3 text-warning">{result.warnings.join(" ")}</p>}
            {result.data && <pre className="mt-3 max-h-48 overflow-auto rounded bg-card p-3 text-xs">{JSON.stringify(result.data, null, 2)}</pre>}
          </div>
        )}
      </div>
    </section>
  )
}
