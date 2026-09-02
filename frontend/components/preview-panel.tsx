"use client"

import useSWR from "swr"
import { X, ExternalLink, Loader2, Folder, FileText, Copy, Check } from "lucide-react"
import { useState } from "react"
import type { PreviewResponse, SearchResult } from "@/lib/types"
import { formatBytes, formatDateTime } from "@/lib/format"
import { cn } from "@/lib/utils"

const fetcher = (url: string) =>
  fetch(url).then(async (r) => {
    const body = await r.json()
    if (!r.ok) throw new Error(body?.detail ?? "Preview failed.")
    return body as PreviewResponse
  })

interface PreviewPanelProps {
  result: SearchResult
  onClose: () => void
  onOpen: (r: SearchResult) => void
  onSelectChild?: (path: string) => void
}

export function PreviewPanel({ result, onClose, onOpen }: PreviewPanelProps) {
  const { data, error, isLoading } = useSWR(`/api/preview?path=${encodeURIComponent(result.path)}`, fetcher)
  const [copied, setCopied] = useState(false)

  function copyPath() {
    navigator.clipboard?.writeText(result.path).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <aside className="flex h-full flex-col bg-card">
      <header className="flex items-start justify-between gap-3 border-b border-border p-4">
        <div className="flex min-w-0 items-start gap-3">
          <div
            className={cn(
              "flex size-9 shrink-0 items-center justify-center rounded-md",
              result.is_dir ? "bg-primary/10 text-primary" : "bg-white/5 text-muted-foreground",
            )}
          >
            {result.is_dir ? <Folder className="size-5" /> : <FileText className="size-5" />}
          </div>
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-foreground" title={result.name}>
              {result.name}
            </h2>
            <p className="text-xs uppercase tracking-wide text-muted-foreground">
              {result.is_dir ? "Folder" : result.extension.replace(".", "") || "File"}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close preview"
          className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-white/10 hover:text-foreground"
        >
          <X className="size-4" />
        </button>
      </header>

      {/* Metadata */}
      <div className="grid grid-cols-2 gap-px border-b border-border bg-border">
        <Meta label="Size" value={result.is_dir ? "—" : formatBytes(result.size)} />
        <Meta label="Modified" value={formatDateTime(result.modified)} />
      </div>

      {!result.is_dir && data?.extraction_status && data.extraction_status !== "extracted" && (
        <div className="border-b border-border px-4 py-3 text-xs text-amber-300">
          Content status: {data.extraction_status.replace("_", " ")}
          {data.extraction_detail ? ` (${data.extraction_detail})` : ""}
        </div>
      )}

      <div className="border-b border-border p-4">
        <div className="mb-1 flex items-center justify-between">
          <span className="text-[11px] uppercase tracking-wide text-muted-foreground">Full path</span>
          <button
            type="button"
            onClick={copyPath}
            className="inline-flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
          >
            {copied ? <Check className="size-3 text-success" /> : <Copy className="size-3" />}
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
        <p className="break-all font-mono text-xs leading-relaxed text-foreground/80">{result.path}</p>
      </div>

      {/* Body */}
      <div className="min-h-0 flex-1 overflow-auto">
        {isLoading && (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin text-primary" />
            Loading preview…
          </div>
        )}

        {error && (
          <div className="p-4 text-sm text-muted-foreground">
            {(error as Error).message}
          </div>
        )}

        {data && !data.is_dir && data.text && (
          <pre className="whitespace-pre-wrap p-4 font-mono text-[13px] leading-relaxed text-foreground/80">
            {data.text}
          </pre>
        )}

        {data?.is_dir && data.children && (
          <ul className="divide-y divide-border">
            {data.children.length === 0 && (
              <li className="p-4 text-sm text-muted-foreground">This folder is empty.</li>
            )}
            {data.children.map((c) => (
              <li key={c.path} className="flex items-center gap-2 px-4 py-2.5 text-sm">
                {c.is_dir ? (
                  <Folder className="size-4 shrink-0 text-primary" />
                ) : (
                  <FileText className="size-4 shrink-0 text-muted-foreground" />
                )}
                <span className="truncate text-foreground/90">{c.name}</span>
                {!c.is_dir && (
                  <span className="ml-auto shrink-0 text-xs text-muted-foreground">{formatBytes(c.size)}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Actions */}
      <footer className="border-t border-border p-4">
        <button
          type="button"
          onClick={() => onOpen(result)}
          className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/90"
        >
          <ExternalLink className="size-4" />
          {result.is_dir ? "Open folder on host" : "Open file on host"}
        </button>
      </footer>
    </aside>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-card p-4">
      <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-0.5 text-sm text-foreground">{value}</p>
    </div>
  )
}
