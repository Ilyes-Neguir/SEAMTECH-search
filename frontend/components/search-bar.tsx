"use client"

import type React from "react"
import { useRef } from "react"
import { Search, X, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"

interface SearchBarProps {
  value: string
  onChange: (v: string) => void
  onSubmit: () => void
  loading?: boolean
  autoFocus?: boolean
}

export function SearchBar({ value, onChange, onSubmit, loading, autoFocus }: SearchBarProps) {
  const inputRef = useRef<HTMLInputElement>(null)

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && !e.nativeEvent.isComposing && e.keyCode !== 229) {
      e.preventDefault()
      onSubmit()
    }
  }

  return (
    <div className="flex w-full items-center gap-2">
      <div
        className={cn(
          "group flex h-12 flex-1 items-center gap-3 rounded-lg border border-input bg-card px-4",
          "focus-within:border-primary/60 focus-within:ring-2 focus-within:ring-ring/25",
        )}
      >
        {loading ? (
          <Loader2 className="size-5 shrink-0 animate-spin text-primary" aria-hidden />
        ) : (
          <Search className="size-5 shrink-0 text-muted-foreground group-focus-within:text-primary" aria-hidden />
        )}
        <input
          ref={inputRef}
          value={value}
          autoFocus={autoFocus}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Search files, folders, paths and document content…"
          aria-label="Search"
          className="h-full w-full bg-transparent text-[15px] text-foreground placeholder:text-muted-foreground focus:outline-none"
          spellCheck={false}
          autoComplete="off"
        />
        {value && (
          <button
            type="button"
            onClick={() => {
              onChange("")
              inputRef.current?.focus()
            }}
            aria-label="Clear search"
            className="rounded-md p-1 text-muted-foreground transition-colors hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        )}
      </div>
      <button
        type="button"
        onClick={onSubmit}
        disabled={loading}
        className={cn(
          "h-12 shrink-0 rounded-lg bg-primary px-6 text-sm font-semibold text-primary-foreground",
          "transition-colors hover:bg-primary/90 disabled:opacity-60",
        )}
      >
        Search
      </button>
    </div>
  )
}
