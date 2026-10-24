"use client"

// Barre de navigation commune aux 7 écrans (lot D + K) + déconnexion.

import Link from "next/link"
import { usePathname } from "next/navigation"
import { BarChart3, ClipboardCheck, FilePlus2, FolderOpen, FolderSearch, Search, Shapes } from "lucide-react"
import { SignOutAllButton, SignOutButton } from "@/components/sign-out-button"
import { cn } from "@/lib/utils"

const LIENS = [
  { href: "/recherche", libelle: "Recherche", icone: Search },
  { href: "/dossiers", libelle: "Dossiers", icone: FolderOpen },
  { href: "/fichiers", libelle: "Fichiers", icone: FolderSearch },
  { href: "/nouveau", libelle: "Nouveau dossier", icone: FilePlus2 },
  { href: "/validation", libelle: "Validation", icone: ClipboardCheck },
  { href: "/qualite", libelle: "Qualité", icone: BarChart3 },
  { href: "/gabarits", libelle: "Gabarits", icone: Shapes },
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
      <SignOutAllButton />
      <SignOutButton />
    </nav>
  )
}
