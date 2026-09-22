"use client"

// Barre de navigation commune aux 5 écrans (lot D) + déconnexion.

import Link from "next/link"
import { usePathname } from "next/navigation"
import { ClipboardCheck, FilePlus2, FolderOpen, FolderSearch, LogOut, Search } from "lucide-react"
import { cn } from "@/lib/utils"

const LIENS = [
  { href: "/recherche", libelle: "Recherche", icone: Search },
  { href: "/dossiers", libelle: "Dossiers", icone: FolderOpen },
  { href: "/fichiers", libelle: "Fichiers", icone: FolderSearch },
  { href: "/nouveau", libelle: "Nouveau dossier", icone: FilePlus2 },
  { href: "/validation", libelle: "Validation", icone: ClipboardCheck },
]

export function Nav() {
  const chemin = usePathname()
  return (
    <nav className="flex h-14 items-center gap-1 border-b border-border bg-background px-4" data-testid="nav">
      <span className="mr-4 font-mono text-sm font-bold">SEAMTECH</span>
      {LIENS.map(({ href, libelle, icone: Icone }) => (
        <Link
          key={href}
          href={href}
          className={cn(
            "flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium",
            chemin === href || chemin.startsWith(`${href}/`) ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Icone className="size-3.5" /> {libelle}
        </Link>
      ))}
      <span className="flex-1" />
      <a href="/api/auth/logout" className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
        <LogOut className="size-3.5" /> Quitter
      </a>
    </nav>
  )
}
