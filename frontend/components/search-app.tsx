"use client"

// Recherche historique de l’archive et flux Import. L’état de recherche,
// d’aperçu et d’import reste côté client.

import { useState } from "react"
import type { SearchResult } from "@/lib/types"
import { useSearch } from "@/hooks/use-search"
import { SeamtechLogo } from "@/components/seamtech-logo"
import { IndexStatus } from "@/components/index-status"
import { SearchBar } from "@/components/search-bar"
import { ResultsList } from "@/components/results-list"
import { PreviewPanel } from "@/components/preview-panel"
import { cn } from "@/lib/utils"
import { ImportPanel } from "@/components/import-panel"
import { SignOutButton } from "@/components/sign-out-button"

export function SearchApp() {
  const { query, results, hasMore, loading, loadingMore, searched, error, run, loadMore } = useSearch()
  const [input, setInput] = useState("")
  const [selected, setSelected] = useState<SearchResult | null>(null)

  return (
    <div className="flex min-h-dvh flex-col">
      {/* Header */}
      <header className="sticky top-0 z-20 border-b border-border bg-background/85 backdrop-blur-md">
        <div className="mx-auto w-full max-w-6xl px-4 py-4 sm:px-6">
          <div className="flex items-center justify-between gap-4">
            <SeamtechLogo className="h-8 w-auto sm:h-9" />
            <div className="flex items-center gap-3">
              <div className="hidden text-right text-xs text-muted-foreground sm:block">
                Recherche d’archives
              </div>
              <SignOutButton />
            </div>
          </div>
          <div className="mt-4">
            <SearchBar
              value={input}
              onChange={setInput}
              onSubmit={() => run(input)}
              loading={loading}
              autoFocus
            />
          </div>
          <div className="mt-3 flex items-center justify-between gap-4">
            <IndexStatus />
            {searched && !loading && !error && (
              <span className="shrink-0 text-xs text-muted-foreground">
                {results.length}
                {hasMore ? "+" : ""} résultat{results.length === 1 ? "" : "s"}
                {query && <span className="text-foreground/70"> pour « {query} »</span>}
              </span>
            )}
          </div>
        </div>
      </header>

      <ImportPanel />

      {/* Body */}
      <main className="mx-auto flex w-full max-w-6xl flex-1 gap-0 px-4 sm:px-6">
        <div
          className={cn(
            "min-w-0 flex-1 border-x border-border bg-card/40",
            selected ? "hidden lg:block" : "block",
          )}
        >
          <ResultsList
            results={results}
            selectedId={selected?.id ?? null}
            onSelect={setSelected}
            onLoadMore={loadMore}
            hasMore={hasMore}
            loadingMore={loadingMore}
            query={query}
            loading={loading}
            searched={searched}
            error={error}
          />
        </div>

        {selected && (
          <div className="w-full border-r border-border lg:w-[400px] lg:shrink-0">
            <div className="sticky top-[164px] h-[calc(100dvh-164px)]">
              <PreviewPanel result={selected} onClose={() => setSelected(null)} />
            </div>
          </div>
        )}
      </main>

      <footer className="border-t border-border py-4">
        <div className="mx-auto w-full max-w-6xl px-4 text-center text-xs text-muted-foreground sm:px-6">
          SEAMTECH Search · Tous les documents de votre atelier
        </div>
      </footer>

    </div>
  )
}
