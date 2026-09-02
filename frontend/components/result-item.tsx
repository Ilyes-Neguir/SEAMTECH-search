"use client"

import {
  Folder,
  FileText,
  FileSpreadsheet,
  FileImage,
  FileCode2,
  FileArchive,
  File,
  Eye,
  ExternalLink,
} from "lucide-react"
import type { SearchResult } from "@/lib/types"
import { formatBytes, formatDate } from "@/lib/format"
import { MatchBadge } from "./match-badge"
import { cn } from "@/lib/utils"

function iconFor(r: SearchResult) {
  if (r.is_dir) return Folder
  const e = r.extension.replace(".", "").toLowerCase()
  if (["xls", "xlsx", "csv"].includes(e)) return FileSpreadsheet
  if (["doc", "docx", "pdf", "txt", "rtf", "odt"].includes(e)) return FileText
  if (["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "tif", "tiff"].includes(e)) return FileImage
  if (["dxf", "dwg", "step", "stp", "igs"].includes(e)) return FileCode2
  if (["zip", "rar", "7z", "tar", "gz"].includes(e)) return FileArchive
  return File
}

function extractionLabel(status?: SearchResult["extraction_status"]) {
  if (!status || status === "extracted" || status === "not_applicable") return null
  return status.replace("_", " ")
}

function renderSnippet(snippet: string) {
  let highlighted = false
  return snippet.split(/(<mark>|<\/mark>|<em>|<\/em>)/g).map((part, index) => {
    if (part === "<mark>" || part === "<em>") {
      highlighted = true
      return null
    }
    if (part === "</mark>" || part === "</em>") {
      highlighted = false
      return null
    }
    return highlighted ? <mark key={index}>{part}</mark> : <span key={index}>{part}</span>
  })
}

interface ResultItemProps {
  result: SearchResult
  selected: boolean
  onSelect: () => void
  onOpen: () => void
}

export function ResultItem({ result, selected, onSelect, onOpen }: ResultItemProps) {
  const Icon = iconFor(result)
  const showSnippet = result.match_type === "content" && result.snippet

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault()
          onSelect()
        }
      }}
      className={cn(
        "group flex cursor-pointer items-start gap-3 border-l-2 px-4 py-3 transition-colors",
        selected
          ? "border-l-primary bg-primary/5"
          : "border-l-transparent hover:border-l-primary/40 hover:bg-white/[0.03]",
      )}
    >
      <div
        className={cn(
          "mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-md",
          result.is_dir ? "bg-primary/10 text-primary" : "bg-white/5 text-muted-foreground",
        )}
      >
        <Icon className="size-5" />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h3 className="truncate text-[15px] font-medium text-foreground">{result.name}</h3>
          <MatchBadge type={result.match_type} />
          {extractionLabel(result.extraction_status) && (
            <span className="shrink-0 text-[10px] uppercase tracking-wide text-amber-300">
              {extractionLabel(result.extraction_status)}
            </span>
          )}
        </div>

        <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground" title={result.parent}>
          {result.parent}
        </p>

        {showSnippet && (
          <p
            className="snippet mt-1.5 line-clamp-2 text-[13px] leading-relaxed text-muted-foreground"
          >
            {renderSnippet(result.snippet)}
          </p>
        )}

        <div className="mt-1.5 flex items-center gap-3 text-[11px] text-muted-foreground">
          <span className="uppercase">{result.is_dir ? "Folder" : result.extension.replace(".", "") || "File"}</span>
          {!result.is_dir && (
            <>
              <span aria-hidden>·</span>
              <span>{formatBytes(result.size)}</span>
            </>
          )}
          <span aria-hidden>·</span>
          <span>{formatDate(result.modified)}</span>
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            onSelect()
          }}
          aria-label="Preview"
          title="Preview"
          className="rounded-md p-2 text-muted-foreground transition-colors hover:bg-white/10 hover:text-foreground"
        >
          <Eye className="size-4" />
        </button>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            onOpen()
          }}
          aria-label="Open on host"
          title="Open on host"
          className="rounded-md p-2 text-muted-foreground transition-colors hover:bg-white/10 hover:text-foreground"
        >
          <ExternalLink className="size-4" />
        </button>
      </div>
    </div>
  )
}
