"use client"

import { SearchX, Loader2, FileSearch } from "lucide-react"
import type { SearchResult } from "@/lib/types"
import { ResultItem } from "./result-item"

interface ResultsListProps {
  results: SearchResult[]
  selectedId: number | null
  onSelect: (r: SearchResult) => void
  onLoadMore?: () => void
  hasMore?: boolean
  loadingMore?: boolean
  query: string
  loading: boolean
  searched: boolean
  error?: string | null
}

export function ResultsList({
  results,
  selectedId,
  onSelect,
  onLoadMore,
  hasMore,
  loadingMore,
  loading,
  searched,
  error,
}: ResultsListProps) {
  if (loading && results.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-24 text-muted-foreground">
        <Loader2 className="size-6 animate-spin text-primary" />
        <p className="text-sm">Recherche dans l’index…</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 px-6 py-24 text-center">
        <SearchX className="size-8 text-destructive" />
        <p className="text-sm font-medium text-foreground">Échec de la recherche</p>
        <p className="max-w-sm text-sm text-muted-foreground">{error}</p>
      </div>
    )
  }

  if (!searched) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 px-6 py-24 text-center">
        <div className="flex size-14 items-center justify-center rounded-full bg-primary/10 text-primary">
          <FileSearch className="size-7" />
        </div>
        <p className="text-sm font-medium text-foreground">Rechercher dans l’archive SEAMTECH</p>
        <p className="max-w-md text-sm text-muted-foreground">
          Retrouvez fiches, plans, documents techniques et commandes par nom ou contenu.
        </p>
      </div>
    )
  }

  if (results.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 px-6 py-24 text-center">
        <SearchX className="size-8 text-muted-foreground" />
        <p className="text-sm font-medium text-foreground">Aucun résultat trouvé</p>
        <p className="max-w-sm text-sm text-muted-foreground">
          Essayez un autre terme, une partie du nom de fichier ou une référence de commande.
        </p>
      </div>
    )
  }

  return (
    <div className="divide-y divide-border">
      {results.map((r) => (
        <ResultItem
          key={r.id}
          result={r}
          selected={selectedId === r.id}
          onSelect={() => onSelect(r)}
        />
      ))}

      {hasMore && (
        <div className="flex justify-center p-4">
          <button
            type="button"
            onClick={onLoadMore}
            disabled={loadingMore}
            className="inline-flex items-center gap-2 rounded-lg border border-input bg-card px-5 py-2 text-sm font-medium text-foreground transition-colors hover:bg-white/5 disabled:opacity-60"
          >
            {loadingMore && <Loader2 className="size-4 animate-spin" />}
            Charger plus de résultats
          </button>
        </div>
      )}
    </div>
  )
}
