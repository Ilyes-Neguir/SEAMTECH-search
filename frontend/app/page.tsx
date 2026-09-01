"use client"

import { useCallback, useEffect, useState } from "react"
import type { SearchResult } from "@/lib/types"
import { useSearch } from "@/hooks/use-search"
import { SeamtechLogo } from "@/components/seamtech-logo"
import { IndexStatus } from "@/components/index-status"
import { SearchBar } from "@/components/search-bar"
import { ResultsList } from "@/components/results-list"
import { PreviewPanel } from "@/components/preview-panel"
import { cn } from "@/lib/utils"

export default function Page() {
  const { query, results, hasMore, loading, loadingMore, searched, error, run, loadMore } = useSearch()
  const [input, setInput] = useState("")
  const [selected, setSelected] = useState<SearchResult | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(t)
  }, [toast])

  const onOpen = useCallback(async (r: SearchResult) => {
    try {
      const res = await fetch(`/api/open?path=${encodeURIComponent(r.path)}`, { method: "POST" })
      const body = await res.json().catch(() => ({}))
      if (res.ok) {
        setToast(`Opening ${r.is_dir ? "folder" : "file"} on the host…`)
      } else {
        setToast(body?.detail ?? "Could not open on host.")
      }
    } catch {
      setToast("Could not reach the SEAMTECH backend.")
    }
  }, [])

  return (
    <div className="flex min-h-dvh flex-col">
      {/* Header */}
      <header className="sticky top-0 z-20 border-b border-border bg-background/85 backdrop-blur-md">
        <div className="mx-auto w-full max-w-6xl px-4 py-4 sm:px-6">
          <div className="flex items-center justify-between gap-4">
            <SeamtechLogo className="h-8 w-auto sm:h-9" />
            <div className="hidden text-right text-xs text-muted-foreground sm:block">
              Internal File Search
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
                {hasMore ? "+" : ""} result{results.length === 1 ? "" : "s"}
                {query && <span className="text-foreground/70"> for “{query}”</span>}
              </span>
            )}
          </div>
        </div>
      </header>

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
            selectedPath={selected?.path ?? null}
            onSelect={setSelected}
            onOpen={onOpen}
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
              <PreviewPanel result={selected} onClose={() => setSelected(null)} onOpen={onOpen} />
            </div>
          </div>
        )}
      </main>

      <footer className="border-t border-border py-4">
        <div className="mx-auto w-full max-w-6xl px-4 text-center text-xs text-muted-foreground sm:px-6">
          SEAMTECH Search · We produce your sails
        </div>
      </footer>

      {/* Toast */}
      <div aria-live="polite" className="pointer-events-none fixed inset-x-0 bottom-6 z-50 flex justify-center px-4">
        {toast && (
          <div className="pointer-events-auto max-w-md rounded-lg border border-border bg-card px-4 py-3 text-sm text-foreground shadow-lg shadow-black/40">
            {toast}
          </div>
        )}
      </div>
    </div>
  )
}
