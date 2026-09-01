"use client"

import { SearchX, Loader2, FileSearch } from "lucide-react"
import type { SearchResult } from "@/lib/types"
import { ResultItem } from "./result-item"

interface ResultsListProps {
  results: SearchResult[]
  selectedPath: string | null
  onSelect: (r: SearchResult) => void
  onOpen: (r: SearchResult) => void
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
  selectedPath,
  onSelect,
  onOpen,
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
        <p className="text-sm">Searching the index…</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 px-6 py-24 text-center">
        <SearchX className="size-8 text-destructive" />
        <p className="text-sm font-medium text-foreground">Search failed</p>
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
        <p className="text-sm font-medium text-foreground">Search the SEAMTECH archive</p>
        <p className="max-w-md text-sm text-muted-foreground">
          Find sails, patterns, datasheets and client orders by name, folder path or by the text inside documents.
        </p>
      </div>
    )
  }

  if (results.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 px-6 py-24 text-center">
        <SearchX className="size-8 text-muted-foreground" />
        <p className="text-sm font-medium text-foreground">No matches found</p>
        <p className="max-w-sm text-sm text-muted-foreground">
          Try a different term, a partial file name, or an order reference like SAIL-2041.
        </p>
      </div>
    )
  }

  return (
    <div className="divide-y divide-border">
      {results.map((r) => (
        <ResultItem
          key={r.path}
          result={r}
          selected={selectedPath === r.path}
          onSelect={() => onSelect(r)}
          onOpen={() => onOpen(r)}
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
            Load more results
          </button>
        </div>
      )}
    </div>
  )
}
