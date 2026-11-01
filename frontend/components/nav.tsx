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
      {/*
        Pas de `aria-label` ici : le nom accessible est le texte VISIBLE
        (« Seamtech / voilerie · archives »). C'est l'exigence
        WCAG 2.5.3 (label-content-name-mismatch) : un libellé qui ne reprend
        pas le texte affiché casse la commande vocale — l'utilisateur dit
        « Seamtech » et rien ne s'active.
      */}
      <Link href="/recherche" className="group mr-3 flex shrink-0 items-center gap-2">
