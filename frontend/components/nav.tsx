"use client"

// Barre de navigation commune + identité/rôle de la session (Lot 1).
// Lot 8 : liens groupés par domaine de la voilerie (Explorer / Atelier /
// Pilotage), coutures entre groupes, bascule de quart — les visibilités par
// rôle et les libellés sont INCHANGÉS.

import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  BarChart3,
  ClipboardCheck,
  ClipboardList,
  FilePlus2,
  FolderOpen,
  FolderSearch,
  Gauge,
  Sailboat,
  Scale,
  Search,
  Shapes,
  Users,
} from "lucide-react"
import { BasculeTheme } from "@/components/bascule-theme"
import { NotificationsBell } from "@/components/notifications-bell"
import { RoseDesVents } from "@/components/rose-des-vents"
import { SignOutAllButton, SignOutButton } from "@/components/sign-out-button"
import {
  estAdmin,
  estApprobateur,
  peutDeposer,
  roleLibelle,
} from "@/lib/roles"
import { useSession } from "@/lib/use-session"
import { cn } from "@/lib/utils"

type Groupe = "explorer" | "atelier" | "pilotage"

const GROUPES: { code: Groupe; libelle: string }[] = [
  { code: "explorer", libelle: "Explorer" },
  { code: "atelier", libelle: "Atelier" },
  { code: "pilotage", libelle: "Pilotage" },
]

export function Nav() {
  const chemin = usePathname()
  const { session, role } = useSession()
  const nominatif = session.mode === "nominatif"

  const tousLesLiens: { href: string; libelle: string; icone: typeof Search; groupe: Groupe; visible: boolean }[] = [
    { href: "/recherche", libelle: "Recherche", icone: Search, groupe: "explorer", visible: true },
    { href: "/dossiers", libelle: "Dossiers", icone: FolderOpen, groupe: "explorer", visible: true },
    { href: "/fichiers", libelle: "Fichiers", icone: FolderSearch, groupe: "explorer", visible: true },
    { href: "/nouveau", libelle: "Nouveau dossier", icone: FilePlus2, groupe: "atelier", visible: peutDeposer(role) },
    {
      href: "/validation",
      libelle: "Validation",
      icone: ClipboardCheck,
      groupe: "atelier",
      visible: nominatif && estApprobateur(role),
    },
    {
      href: "/extractions",
      libelle: "Extractions",
      icone: Sailboat,
      groupe: "atelier",
      visible: nominatif && estApprobateur(role),
    },
    { href: "/comparaison", libelle: "Comparaison", icone: Scale, groupe: "atelier", visible: nominatif },
    { href: "/confiance", libelle: "Confiance", icone: Gauge, groupe: "atelier", visible: nominatif },
    { href: "/approvals", libelle: "Demandes", icone: ClipboardList, groupe: "pilotage", visible: nominatif },
    { href: "/qualite", libelle: "Qualité", icone: BarChart3, groupe: "pilotage", visible: true },
    { href: "/gabarits", libelle: "Gabarits", icone: Shapes, groupe: "pilotage", visible: estAdmin(role) },
    { href: "/comptes", libelle: "Comptes", icone: Users, groupe: "pilotage", visible: estAdmin(role) },
  ]
  const liens = tousLesLiens.filter((lien) => lien.visible)

  const groupesVisibles = GROUPES.filter((g) => liens.some((l) => l.groupe === g.code))

  return (
    <nav
      className="relative flex h-14 items-center gap-1 overflow-x-auto bg-background/95 px-4"
      data-testid="nav"
    >
      <Link href="/recherche" className="group mr-3 flex shrink-0 items-center gap-2" aria-label="SEAMTECH — accueil de l’atelier">
        <RoseDesVents className="size-7 text-primary transition-transform duration-700 group-hover:rotate-45" />
        <span className="leading-none">
          <span className="titre-voile block text-[13px]">Seamtech</span>
          <span className="mt-0.5 block text-[8.5px] uppercase tracking-[0.22em] text-muted-foreground">
            voilerie · archives
          </span>
        </span>
      </Link>

      {groupesVisibles.map((groupe, indice) => (
        <span key={groupe.code} className="contents">
          {indice > 0 && <span className="couture-verticale mx-1.5 hidden shrink-0 sm:block" aria-hidden />}
          <span
            className="ml-1 mr-0.5 hidden shrink-0 text-[8.5px] font-semibold uppercase tracking-[0.2em] text-muted-foreground/80 lg:block"
            data-testid={`nav-groupe-${groupe.code}`}
          >
            {groupe.libelle}
          </span>
          {liens
            .filter((lien) => lien.groupe === groupe.code)
            .map(({ href, libelle, icone: Icone }) => {
              const actif = chemin === href || chemin.startsWith(`${href}/`)
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={actif ? "page" : undefined}
                  className={cn(
                    "relative flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded px-3 py-1.5 text-xs font-medium transition-colors",
                    actif
                      ? "bg-accent text-foreground shadow-[inset_0_-2px_0_var(--primary)]"
                      : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                  )}
                >
                  <Icone className={cn("size-3.5", actif && "text-primary")} aria-hidden /> {libelle}
                </Link>
              )
            })}
        </span>
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
      <BasculeTheme />
      <NotificationsBell />
      <SignOutAllButton />
      <SignOutButton />
      {/* L'horizon : le fil d'insigne court sous toute la barre. */}
      <span className="couture pointer-events-none absolute inset-x-0 bottom-0 opacity-70" aria-hidden />
    </nav>
  )
}
