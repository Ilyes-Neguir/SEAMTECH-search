"use client"

import { useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { FilePenLine, Trash2 } from "lucide-react"
import { creerDemande } from "@/lib/approbations"
import { authedFetch } from "@/lib/authed-fetch"
import type { ChampExtrait } from "@/lib/fiche"
import { peutDemander } from "@/lib/roles"
import { useSession } from "@/lib/use-session"

type EtatFiche = {
  code: string
  statut: string
  revision: number
  champs: ChampExtrait[]
}

async function chargerEtat(code: string): Promise<EtatFiche> {
  const response = await authedFetch(`/api/fiches/${encodeURIComponent(code)}/etat`, { cache: "no-store" })
  const corps = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(String(corps?.detail ?? `HTTP ${response.status}`))
  return corps as EtatFiche
}

function cleChamp(champ: ChampExtrait): string {
  return `${champ.champ}#${champ.rang ?? ""}`
}

export function ApprovalRequestActions({ code }: { code: string }) {
  const { session, role, chargement: chargementSession } = useSession()
  const [etat, setEtat] = useState<EtatFiche | null>(null)
  const [chargement, setChargement] = useState(true)
  const [cleSelectionnee, setCleSelectionnee] = useState("")
  const [valeurProposee, setValeurProposee] = useState("")
  const [motifModification, setMotifModification] = useState("")
  const [motifSuppression, setMotifSuppression] = useState("")
  const [confirmationSuppression, setConfirmationSuppression] = useState(false)
  const [envoi, setEnvoi] = useState<"modification" | "suppression" | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)

  const autorise = session.mode === "nominatif" && peutDemander(role)

  useEffect(() => {
    if (!autorise) {
      setChargement(false)
      return
    }
    let actif = true
    setChargement(true)
    chargerEtat(code)
      .then((corps) => {
        if (!actif) return
        setEtat(corps)
        const premier = corps.champs[0]
        if (premier) {
          setCleSelectionnee(cleChamp(premier))
          setValeurProposee(premier.valeur_normalisee ?? premier.valeur_brute ?? "")
        }
      })
      .catch((error) => {
        if (actif) setErreur(error instanceof Error ? error.message : "État de fiche indisponible.")
      })
      .finally(() => {
        if (actif) setChargement(false)
      })
    return () => {
      actif = false
    }
  }, [autorise, code])

  const champSelectionne = useMemo(
    () => etat?.champs.find((champ) => cleChamp(champ) === cleSelectionnee) ?? null,
    [etat, cleSelectionnee],
  )

  if (chargementSession || !autorise) return null

  async function demanderModification(event: React.FormEvent) {
    event.preventDefault()
    if (!etat || !champSelectionne) return
    setEnvoi("modification")
    setErreur(null)
    setMessage(null)
    try {
      await creerDemande({
        type_demande: "modification",
        code_fiche: code,
        motif: motifModification.trim(),
        modification: {
          champ: champSelectionne.champ,
          rang: champSelectionne.rang,
          valeur: valeurProposee,
          revision: etat.revision,
        },
      })
      setMessage("Demande de modification envoyée à un approbateur.")
      setMotifModification("")
    } catch (error) {
      setErreur(error instanceof Error ? error.message : "La demande de modification a échoué.")
    } finally {
      setEnvoi(null)
    }
  }

  async function demanderSuppression(event: React.FormEvent) {
    event.preventDefault()
    if (!etat || !confirmationSuppression) return
    setEnvoi("suppression")
    setErreur(null)
    setMessage(null)
    try {
      await creerDemande({
        type_demande: "suppression",
        code_fiche: code,
        motif: motifSuppression.trim(),
        revision: etat.revision,
      })
      setMessage("Demande de suppression logique envoyée à un approbateur.")
      setMotifSuppression("")
      setConfirmationSuppression(false)
    } catch (error) {
      setErreur(error instanceof Error ? error.message : "La demande de suppression a échoué.")
    } finally {
      setEnvoi(null)
    }
  }

  return (
    <section className="border-b border-border p-4" data-testid="demandes-fiche">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">Demander une décision</h2>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            Votre demande ne modifie rien immédiatement : seul un approbateur peut l’exécuter.
          </p>
        </div>
        <Link href="/approvals?vue=miennes" className="text-[11px] font-medium text-primary hover:underline">
          Mes demandes
        </Link>
      </div>

      {chargement && <p className="mt-3 text-xs text-muted-foreground">Chargement de la révision affichée…</p>}
      {message && (
        <p className="mt-3 rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200" role="status">
          {message}
        </p>
      )}
      {erreur && (
        <p className="mt-3 rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive" role="alert">
          {erreur}
        </p>
      )}

      {etat && etat.statut === "supprimee" && (
        <p className="mt-3 text-xs text-muted-foreground">Cette fiche est supprimée logiquement : aucune nouvelle demande n’est possible.</p>
      )}

      {etat && etat.statut !== "supprimee" && (
        <div className="mt-4 space-y-5">
          <form onSubmit={demanderModification} className="space-y-2" data-testid="demande-modification-form">
            <h3 className="flex items-center gap-2 text-xs font-semibold">
              <FilePenLine className="size-4 text-primary" aria-hidden /> Modification d’un champ
            </h3>
            {etat.statut === "valide" ? (
              <p className="text-[11px] text-amber-200">
                RG11 : une fiche validée doit d’abord être rouverte par un approbateur avant toute proposition de modification.
              </p>
            ) : etat.champs.length === 0 ? (
              <p className="text-[11px] text-muted-foreground">Aucun champ extrait ne peut être proposé en modification.</p>
            ) : (
              <>
                <label className="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                  Champ à modifier
                  <select
                    value={cleSelectionnee}
                    onChange={(event) => {
                      const cle = event.target.value
                      setCleSelectionnee(cle)
                      const champ = etat.champs.find((item) => cleChamp(item) === cle)
                      setValeurProposee(champ?.valeur_normalisee ?? champ?.valeur_brute ?? "")
                    }}
                    className="mt-1 w-full rounded border border-border bg-input px-2 py-1.5 text-xs normal-case tracking-normal text-foreground"
                  >
                    {etat.champs.map((champ) => (
                      <option key={cleChamp(champ)} value={cleChamp(champ)}>
                        {champ.champ}{champ.rang != null ? ` [${champ.rang}]` : ""} — {champ.valeur_normalisee ?? "vide"}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                  Valeur proposée
                  <input
                    value={valeurProposee}
                    onChange={(event) => setValeurProposee(event.target.value)}
                    maxLength={2000}
                    className="mt-1 w-full rounded border border-border bg-input px-2 py-1.5 text-xs normal-case tracking-normal text-foreground"
                  />
                </label>
                <label className="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                  Motif de la modification
                  <textarea
                    value={motifModification}
                    onChange={(event) => setMotifModification(event.target.value)}
                    minLength={3}
                    maxLength={1000}
                    required
                    rows={2}
                    className="mt-1 w-full rounded border border-border bg-input px-2 py-1.5 text-xs normal-case tracking-normal text-foreground"
                  />
                </label>
                <button
                  type="submit"
                  disabled={envoi !== null || motifModification.trim().length < 3}
                  className="rounded bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:opacity-50"
                >
                  {envoi === "modification" ? "Envoi…" : "Envoyer la demande de modification"}
                </button>
              </>
            )}
          </form>

          <form onSubmit={demanderSuppression} className="space-y-2 border-t border-border pt-4" data-testid="demande-suppression-form">
            <h3 className="flex items-center gap-2 text-xs font-semibold">
              <Trash2 className="size-4 text-destructive" aria-hidden /> Suppression logique de la fiche
            </h3>
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              La fiche sortira de la recherche et de la validation. Les fichiers sources de l’archive ne seront pas effacés.
            </p>
            <label className="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Motif de la suppression
              <textarea
                value={motifSuppression}
                onChange={(event) => setMotifSuppression(event.target.value)}
                minLength={3}
                maxLength={1000}
                required
                rows={2}
                className="mt-1 w-full rounded border border-border bg-input px-2 py-1.5 text-xs normal-case tracking-normal text-foreground"
              />
            </label>
            <label className="flex items-start gap-2 text-[11px] leading-relaxed text-muted-foreground">
              <input
                type="checkbox"
                checked={confirmationSuppression}
                onChange={(event) => setConfirmationSuppression(event.target.checked)}
                className="mt-0.5"
              />
              Je confirme demander la suppression logique de cette fiche, sans effacer les fichiers sources.
            </label>
            <button
              type="submit"
              disabled={envoi !== null || motifSuppression.trim().length < 3 || !confirmationSuppression}
              className="rounded bg-destructive px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
            >
              {envoi === "suppression" ? "Envoi…" : "Envoyer la demande de suppression"}
            </button>
          </form>
        </div>
      )}
    </section>
  )
}
