"use client"

// Barre de navigation commune aux 7 écrans (lot D + K) + déconnexion.
//
// Lot 0a : la déconnexion n'est PLUS un lien GET. `GET /api/auth/logout`
// répondait 405 (le handler n'accepte que POST) et le cookie httpOnly n'était
// jamais supprimé. Ici on appelle fetch POST avec le jeton CSRF, puis on
// remplace l'historique par /login.

import { useState } from "react"
import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import { BarChart3, ClipboardCheck, FilePlus2, FolderOpen, FolderSearch, LogOut, MonitorSmartphone, Search, Shapes } from "lucide-react"
import { cn } from "@/lib/utils"
import { withCsrf } from "@/lib/csrf"

const LIENS = [
  { href: "/recherche", libelle: "Recherche", icone: Search },
  { href: "/dossiers", libelle: "Dossiers", icone: FolderOpen },
  { href: "/fichiers", libelle: "Fichiers", icone: FolderSearch },
  { href: "/nouveau", libelle: "Nouveau dossier", icone: FilePlus2 },
  { href: "/validation", libelle: "Validation", icone: ClipboardCheck },
  { href: "/qualite", libelle: "Qualité", icone: BarChart3 },
  { href: "/gabarits", libelle: "Gabarits", icone: Shapes },
  { href: "/approbations", libelle: "Approbations", icone: ClipboardCheck },
  { href: "/extractions", libelle: "Extractions", icone: FilePlus2 },
  { href: "/compare", libelle: "Comparer", icone: Shapes },
  { href: "/confiance", libelle: "Confiance", icone: BarChart3 },
]

type Action = "logout" | "logout-all"

export function Nav() {
  const chemin = usePathname()
  const router = useRouter()
  const [enCours, setEnCours] = useState<Action | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)

  // L'écran de connexion n'a pas de session : y afficher « Quitter » et
  // « Déconnecter tous mes appareils » n'aurait aucun sens — et le POST serait
  // refusé faute de jeton CSRF (403). La barre ne doit pas y apparaître.
  if (chemin === "/login") return null

  async function deconnecter(action: Action) {
    if (enCours) return
    setEnCours(action)
    setErreur(null)
    const url = action === "logout" ? "/api/auth/logout" : "/api/auth/logout-all"
    try {
      const reponse = await fetch(
        url,
        withCsrf({
          method: "POST",
          credentials: "include",
          cache: "no-store",
        }),
      )
      if (!reponse.ok && action === "logout-all") {
        // 503 = Redis indisponible : les autres appareils n'ont PAS été
        // déconnectés. On le dit, mais on quitte quand même cet appareil.
        const corps = await reponse.json().catch(() => ({}))
        setErreur(corps?.detail ?? `Révocation globale refusée (${reponse.status}).`)
      }
    } catch {
      if (action === "logout-all") setErreur("Backend injoignable : révocation globale non confirmée.")
    } finally {
      setEnCours(null)
      router.replace("/login")
      router.refresh()
    }
  }

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
      {erreur ? (
        <span role="alert" className="mr-2 max-w-64 truncate text-xs text-destructive" title={erreur}>
          {erreur}
        </span>
      ) : null}
      <button
        type="button"
        onClick={() => deconnecter("logout-all")}
        disabled={enCours !== null}
        data-testid="logout-all"
        className="flex items-center gap-1.5 rounded px-2 py-1.5 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
      >
        <MonitorSmartphone className="size-3.5" /> Déconnecter tous mes appareils
      </button>
      <button
        type="button"
        onClick={() => deconnecter("logout")}
        disabled={enCours !== null}
        data-testid="logout"
        className="flex items-center gap-1.5 rounded px-2 py-1.5 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
      >
        <LogOut className="size-3.5" /> {enCours === "logout" ? "Déconnexion…" : "Quitter"}
      </button>
    </nav>
  )
}
