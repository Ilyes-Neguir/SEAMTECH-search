"use client"

// Barre de navigation commune + identité/rôle de la session (Lot 1).

import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  BarChart3,
  ClipboardCheck,
  ClipboardList,
  FilePlus2,
  FolderOpen,
  FolderSearch,
  Search,
  Shapes,
  Users,
} from "lucide-react"
import { NotificationsBell } from "@/components/notifications-bell"
import { SignOutAllButton, SignOutButton } from "@/components/sign-out-button"
import {
  estAdmin,
  estApprobateur,
  peutDeposer,
  roleLibelle,
} from "@/lib/roles"
import { useSession } from "@/lib/use-session"
import { cn } from "@/lib/utils"

export function Nav() {
  const chemin = usePathname()
  const { session, role } = useSession()
  const nominatif = session.mode === "nominatif"

  const liens = [
    { href: "/recherche", libelle: "Recherche", icone: Search, visible: true },
    { href: "/dossiers", libelle: "Dossiers", icone: FolderOpen, visible: true },
    { href: "/fichiers", libelle: "Fichiers", icone: FolderSearch, visible: true },
    { href: "/nouveau", libelle: "Nouveau dossier", icone: FilePlus2, visible: peutDeposer(role) },
    {
      href: "/validation",
      libelle: "Validation",
      icone: ClipboardCheck,
      visible: nominatif && estApprobateur(role),
    },
    { href: "/approvals", libelle: "Demandes", icone: ClipboardList, visible: nominatif },
    { href: "/qualite", libelle: "Qualité", icone: BarChart3, visible: true },
    { href: "/gabarits", libelle: "Gabarits", icone: Shapes, visible: estAdmin(role) },
    { href: "/comptes", libelle: "Comptes", icone: Users, visible: estAdmin(role) },
  ].filter((lien) => lien.visible)

  return (
    <nav
      className="flex h-14 items-center gap-1 overflow-x-auto border-b border-border bg-background px-4"
      data-testid="nav"
    >
      <span className="mr-4 shrink-0 font-mono text-sm font-bold">SEAMTECH</span>
      {liens.map(({ href, libelle, icone: Icone }) => (
        <Link
          key={href}
          href={href}
          className={cn(
            "flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded px-3 py-1.5 text-xs font-medium",
            chemin === href || chemin.startsWith(`${href}/`)
              ? "bg-accent text-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Icone className="size-3.5" aria-hidden /> {libelle}
        </Link>
      ))}
      <span className="min-w-4 flex-1" />
      {session.authenticated && session.identifiant && (
        <span
          className="mr-2 hidden shrink-0 items-center gap-2 rounded-full border border-border px-2.5 py-1 text-[10px] text-muted-foreground md:inline-flex"
          data-testid="session-identite"
          title={`${session.identifiant} · ${roleLibelle(role)}`}
        >
          <span className="font-medium text-foreground">{session.identifiant}</span>
          <span>{session.mode === "secours" ? "Secours admin" : roleLibelle(role)}</span>
        </span>
      )}
      <NotificationsBell />
      <SignOutAllButton />
      <SignOutButton />
    </nav>
  )
}
