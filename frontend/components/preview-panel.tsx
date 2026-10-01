"use client"

import { Folder, X } from "lucide-react"
import { VisionneusePiece } from "@/components/visionneuse-piece"
import type { PieceJointe } from "@/lib/fiche"
import type { SearchResult } from "@/lib/types"
import { formatBytes, formatDateTime } from "@/lib/format"

const EXTENSIONS_EXCEL = new Set([".csv", ".xls", ".xlsx", ".xlsm", ".ods", ".xlsb"])
const EXTENSIONS_MACHINE = new Set([".dxf", ".dwg", ".step", ".stp", ".igs", ".iges", ".xin", ".plx", ".plt", ".nc", ".cnc"])
const EXTENSIONS_PREVISUALISABLES = new Set([
  ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff",
  ".txt", ".csv", ".md", ".log", ".json", ".xml",
])

function pieceDepuisRecherche(result: SearchResult): PieceJointe {
  const extension = result.extension.toLowerCase()
  const kind: PieceJointe["kind"] = extension === ".pdf"
    ? "pdf"
    : EXTENSIONS_EXCEL.has(extension)
      ? "excel"
      : EXTENSIONS_MACHINE.has(extension)
        ? "machine"
        : "other"
  return {
    id: result.id,
    name: result.name,
    extension,
    size: result.size,
    kind,
    is_primary_pdf: false,
    previewable: EXTENSIONS_PREVISUALISABLES.has(extension),
    dossier: result.parent,
  }
}

interface PreviewPanelProps {
  result: SearchResult
  onClose: () => void
}

export function PreviewPanel({ result, onClose }: PreviewPanelProps) {
  if (result.is_dir) {
    return (
      <aside className="flex h-full flex-col bg-card" aria-label="Aperçu du dossier">
        <header className="flex items-start justify-between gap-3 border-b border-border p-4">
          <div className="flex min-w-0 items-start gap-3">
            <Folder className="mt-0.5 size-5 shrink-0 text-primary" />
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold" title={result.name}>{result.name}</h2>
              <p className="text-xs text-muted-foreground">Dossier · {result.parent || "Archive"}</p>
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label="Fermer l’aperçu" className="rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">
            <X className="size-4" />
          </button>
        </header>
        <div className="flex flex-1 items-center justify-center p-6 text-center text-sm text-muted-foreground">
          Les dossiers ne sont pas téléchargeables. Utilisez le catalogue Fichiers pour filtrer les documents.
        </div>
      </aside>
    )
  }

  const piece = pieceDepuisRecherche(result)
  return (
    <aside className="flex h-full flex-col bg-card" aria-label="Aperçu du fichier">
      <div className="flex justify-end border-b border-border px-2 py-1">
        <button type="button" onClick={onClose} aria-label="Fermer l’aperçu" className="rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground">
          <X className="size-4" />
        </button>
      </div>
      <div className="min-h-0 flex-1">
        <VisionneusePiece piece={piece} />
      </div>
      <div className="grid grid-cols-2 gap-px border-t border-border bg-border text-xs">
        <Meta label="Taille" value={formatBytes(result.size)} />
        <Meta label="Modifié le" value={formatDateTime(result.modified)} />
      </div>
    </aside>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-card p-3">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-0.5 text-xs text-foreground">{value}</p>
    </div>
  )
}
