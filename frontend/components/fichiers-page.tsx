"use client"

import { useState } from "react"
import { Archive, FolderSearch } from "lucide-react"
import { ArchiveBrowser } from "@/components/archive-browser"
import { SearchApp } from "@/components/search-app"
import { cn } from "@/lib/utils"

type ModeFichiers = "catalogue" | "recherche"

export function FichiersPage() {
  const [mode, setMode] = useState<ModeFichiers>("catalogue")
  return (
    <div>
      <div className="flex items-center gap-2 border-b border-border bg-background px-4 py-2.5">
        <span className="mr-2 text-xs font-semibold text-muted-foreground">Fichiers</span>
        <button type="button" onClick={() => setMode("catalogue")} className={cn("inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium", mode === "catalogue" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground")} aria-pressed={mode === "catalogue"}>
          <Archive className="size-3.5" /> Catalogue d’archive
        </button>
        <button type="button" onClick={() => setMode("recherche")} className={cn("inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium", mode === "recherche" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground")} aria-pressed={mode === "recherche"}>
          <FolderSearch className="size-3.5" /> Recherche avancée
        </button>
      </div>
      {mode === "catalogue" ? <ArchiveBrowser /> : <SearchApp />}
    </div>
  )
}
