"use client"

import { useCallback, useEffect, useState } from "react"
import { RefreshCw, ShieldCheck, UserPlus, Users } from "lucide-react"
import { AccesRefuse } from "@/components/acces-refuse"
import { authedFetch } from "@/lib/authed-fetch"
import {
  estAdmin,
  roleLibelle,
  roleOuLecture,
  ROLES,
  type Role,
} from "@/lib/roles"
import { useSession } from "@/lib/use-session"
import { cn } from "@/lib/utils"

type Compte = {
  id_utilisateur: number
  identifiant: string
  nom: string | null
  role: Role
  actif: boolean
  cree_le: string | null
  derniere_connexion: string | null
  doit_changer_mot_de_passe: boolean
  mot_de_passe_defini: boolean
}

async function appel<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await authedFetch(url, {
    ...init,
    cache: "no-store",
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers ?? {}),
    },
  })
  const corps = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(String((corps as Record<string, unknown>)?.detail ?? `HTTP ${response.status}`))
  return corps as T
}

function date(valeur: string | null): string {
  return valeur ? new Date(valeur).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }) : "—"
}

export function ComptesApp() {
  const { session, role, chargement: chargementSession } = useSession()
  const [comptes, setComptes] = useState<Compte[]>([])
  const [rolesChoisis, setRolesChoisis] = useState<Record<string, Role>>({})
  const [chargement, setChargement] = useState(true)
  const [message, setMessage] = useState<string | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)
  const [identifiant, setIdentifiant] = useState("")
  const [nom, setNom] = useState("")
  const [roleCreation, setRoleCreation] = useState<Role>("lecture")
  const [motDePasse, setMotDePasse] = useState("")
  const [creation, setCreation] = useState(false)
  const [roleEnCours, setRoleEnCours] = useState<string | null>(null)

  const charger = useCallback(async () => {
    setChargement(true)
    try {
      const lignes = await appel<Array<Record<string, unknown>>>("/api/auth/utilisateurs")
      const normalises = lignes.map((ligne) => ({
        id_utilisateur: Number(ligne.id_utilisateur),
        identifiant: String(ligne.identifiant),
        nom: typeof ligne.nom === "string" ? ligne.nom : null,
        role: roleOuLecture(ligne.role),
        actif: ligne.actif === true,
        cree_le: typeof ligne.cree_le === "string" ? ligne.cree_le : null,
        derniere_connexion: typeof ligne.derniere_connexion === "string" ? ligne.derniere_connexion : null,
        doit_changer_mot_de_passe: ligne.doit_changer_mot_de_passe === true,
        mot_de_passe_defini: ligne.mot_de_passe_defini === true,
      }))
      setComptes(normalises)
      setRolesChoisis(Object.fromEntries(normalises.map((compte) => [compte.identifiant, compte.role])))
      setErreur(null)
    } catch (error) {
      setErreur(error instanceof Error ? error.message : "Comptes indisponibles.")
    } finally {
      setChargement(false)
    }
  }, [])

  useEffect(() => {
    if (estAdmin(role)) void charger()
  }, [charger, role])

  if (!chargementSession && !estAdmin(role)) {
    return (
      <AccesRefuse message="La gestion des comptes et des rôles est réservée au rôle admin. Un approbateur décide les demandes métier, mais n’administre pas les comptes." />
    )
  }

  async function creerCompte(event: React.FormEvent) {
    event.preventDefault()
    setCreation(true)
    setErreur(null)
    setMessage(null)
    try {
      await appel("/api/auth/utilisateurs", {
        method: "POST",
        body: JSON.stringify({
          identifiant: identifiant.trim(),
          nom: nom.trim(),
          role: roleCreation,
          mot_de_passe: motDePasse,
        }),
      })
      setMessage(`Compte ${identifiant.trim()} créé avec le rôle ${roleLibelle(roleCreation)}. Le mot de passe devra être changé à la première connexion.`)
      setIdentifiant("")
      setNom("")
      setMotDePasse("")
      setRoleCreation("lecture")
      await charger()
    } catch (error) {
      setErreur(error instanceof Error ? error.message : "La création du compte a échoué.")
    } finally {
      setCreation(false)
    }
  }

  async function changerRole(compte: Compte) {
    const nouveau = rolesChoisis[compte.identifiant] ?? compte.role
    if (nouveau === compte.role) return
    setRoleEnCours(compte.identifiant)
    setErreur(null)
    setMessage(null)
    try {
      const resultat = await appel<Record<string, unknown>>(
        `/api/auth/utilisateurs/${encodeURIComponent(compte.identifiant)}/role`,
        { method: "POST", body: JSON.stringify({ role: nouveau }) },
      )
      const sessions = Number(resultat.sessions_revoquees ?? 0)
      setMessage(
        `Rôle de ${compte.identifiant} changé : ${roleLibelle(compte.role)} → ${roleLibelle(nouveau)}. ${sessions} session(s) révoquée(s) immédiatement.`,
      )
      await charger()
    } catch (error) {
      setErreur(error instanceof Error ? error.message : "Le rôle n’a pas pu être changé.")
    } finally {
      setRoleEnCours(null)
    }
  }

  return (
    <main className="mx-auto min-h-[calc(100vh-3.5rem)] w-full max-w-6xl p-4 sm:p-6" data-testid="comptes-app">
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-border pb-4">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-primary">Administration technique</p>
          <h1 className="mt-1 flex items-center gap-2 text-xl font-semibold">
            <Users className="size-5 text-primary" aria-hidden /> Comptes et rôles
          </h1>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted-foreground">
            Quatre rôles seulement : admin technique, lecture, utilisateur et approbateur. Tout changement de rôle est audité
            et révoque immédiatement toutes les sessions du compte concerné.
          </p>
        </div>
        <button type="button" onClick={() => void charger()} className="inline-flex items-center gap-2 rounded border border-border px-3 py-2 text-xs hover:bg-accent">
          <RefreshCw className={cn("size-3.5", chargement && "animate-spin")} aria-hidden /> Actualiser
        </button>
      </header>

      {message && <p className="mt-4 rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200" role="status">{message}</p>}
      {erreur && <p className="mt-4 rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive" role="alert">{erreur}</p>}

      <form onSubmit={creerCompte} className="mt-5 rounded-xl border border-border bg-card p-5" data-testid="compte-creation-form">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <UserPlus className="size-4 text-primary" aria-hidden /> Créer un compte nominatif
        </h2>
        <p className="mt-1 text-[11px] text-muted-foreground">
          Le rôle par défaut est volontairement « Lecture seule ». Choisissez explicitement tout rôle plus puissant.
        </p>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <label className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Identifiant
            <input value={identifiant} onChange={(event) => setIdentifiant(event.target.value)} required autoComplete="off" className="mt-1 w-full rounded border border-border bg-input px-2 py-2 text-xs normal-case tracking-normal text-foreground" />
          </label>
          <label className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Nom
            <input value={nom} onChange={(event) => setNom(event.target.value)} required autoComplete="name" className="mt-1 w-full rounded border border-border bg-input px-2 py-2 text-xs normal-case tracking-normal text-foreground" />
          </label>
          <label className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Rôle
            <select value={roleCreation} onChange={(event) => setRoleCreation(roleOuLecture(event.target.value))} className="mt-1 w-full rounded border border-border bg-input px-2 py-2 text-xs normal-case tracking-normal text-foreground">
              {ROLES.map((item) => <option key={item} value={item}>{roleLibelle(item)}</option>)}
            </select>
          </label>
          <label className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground lg:col-span-2">
            Mot de passe provisoire
            <input type="password" value={motDePasse} onChange={(event) => setMotDePasse(event.target.value)} required minLength={8} autoComplete="new-password" className="mt-1 w-full rounded border border-border bg-input px-2 py-2 text-xs normal-case tracking-normal text-foreground" />
          </label>
        </div>
        <button type="submit" disabled={creation || !identifiant.trim() || !nom.trim() || motDePasse.length < 8} className="mt-4 rounded bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground disabled:opacity-50">
          {creation ? "Création…" : "Créer le compte"}
        </button>
      </form>

      <section className="mt-5 overflow-hidden rounded-xl border border-border bg-card" aria-label="Liste des comptes">
        <div className="border-b border-border px-4 py-3">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Comptes existants</h2>
        </div>
        {chargement && comptes.length === 0 && <p className="p-6 text-sm text-muted-foreground">Chargement des comptes…</p>}
        {!chargement && comptes.length === 0 && <p className="p-6 text-sm text-muted-foreground">Aucun compte nominatif.</p>}
        <ul className="divide-y divide-border/60">
          {comptes.map((compte) => {
            const choix = rolesChoisis[compte.identifiant] ?? compte.role
            const estSoi = session.identifiant === compte.identifiant
            return (
              <li key={compte.id_utilisateur} className="px-4 py-4" data-testid={`compte-${compte.identifiant}`}>
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="min-w-56 flex-1">
                    <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">
                      {compte.identifiant}
                      {!compte.actif && <span className="rounded bg-red-500/15 px-1.5 py-0.5 text-[9px] uppercase text-red-200">Désactivé</span>}
                      {estSoi && <span className="rounded bg-primary/15 px-1.5 py-0.5 text-[9px] uppercase text-primary">Vous</span>}
                    </p>
                    <p className="mt-0.5 text-xs text-muted-foreground">{compte.nom || "Sans nom"} · Créé le {date(compte.cree_le)}</p>
                    <p className="mt-1 text-[10px] text-muted-foreground">
                      Dernière connexion : {date(compte.derniere_connexion)}
                      {compte.doit_changer_mot_de_passe ? " · changement de mot de passe requis" : ""}
                    </p>
                  </div>

                  <div className="flex flex-wrap items-end gap-2">
                    <label className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                      Rôle de {compte.identifiant}
                      <select
                        value={choix}
                        disabled={estSoi}
                        onChange={(event) => setRolesChoisis((actuels) => ({ ...actuels, [compte.identifiant]: roleOuLecture(event.target.value) }))}
                        className="mt-1 block rounded border border-border bg-input px-2 py-2 text-xs normal-case tracking-normal text-foreground disabled:opacity-50"
                      >
                        {ROLES.map((item) => <option key={item} value={item}>{roleLibelle(item)}</option>)}
                      </select>
                    </label>
                    <button
                      type="button"
                      onClick={() => void changerRole(compte)}
                      disabled={estSoi || choix === compte.role || roleEnCours === compte.identifiant}
                      className="inline-flex items-center gap-1.5 rounded bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground disabled:opacity-50"
                    >
                      <ShieldCheck className="size-3.5" aria-hidden />
                      {roleEnCours === compte.identifiant ? "Changement…" : "Changer le rôle"}
                    </button>
                  </div>
                </div>
                {estSoi ? (
                  <p className="mt-2 text-[10px] text-muted-foreground">Vous ne pouvez pas changer votre propre rôle depuis cette session : demandez à un autre admin.</p>
                ) : choix !== compte.role ? (
                  <p className="texte-attention mt-2 text-[10px]" role="alert">
                    Attention : appliquer ce changement déconnectera immédiatement {compte.identifiant} sur tous ses postes.
                  </p>
                ) : null}
              </li>
            )
          })}
        </ul>
      </section>
    </main>
  )
}
