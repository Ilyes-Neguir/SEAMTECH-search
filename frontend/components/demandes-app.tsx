"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { Check, ClipboardList, RefreshCw, X } from "lucide-react"
import { AccesRefuse } from "@/components/acces-refuse"
import { PieceDownload } from "@/components/piece-download"
import {
  annulerDemande,
  chargerDemande,
  chargerDemandes,
  dateLisible,
  deciderDemande,
  statutDemandeLibelle,
  typeDemandeLibelle,
  type DemandeApprobation,
} from "@/lib/approbations"
import { ROLE_APPROBATEUR } from "@/lib/roles"
import { useSession } from "@/lib/use-session"
import { cn } from "@/lib/utils"

const LIMITE = 25

type Vue = "a_decider" | "miennes" | "toutes"

function statutClasses(statut: DemandeApprobation["statut"]): string {
  switch (statut) {
    case "en_attente":
      return "badge-attention"
    case "approuvee":
    case "executee":
      return "border-emerald-500/50 bg-emerald-500/10 text-emerald-200"
    case "refusee":
      return "border-red-500/50 bg-red-500/10 text-red-200"
    default:
      return "border-border bg-accent/40 text-muted-foreground"
  }
}

function objetDemande(demande: DemandeApprobation): string {
  if (demande.code_fiche) return demande.code_fiche
  if (demande.nom_piece) return demande.nom_piece
  if (demande.id_piece) return `Pièce #${demande.id_piece}`
  return "Objet non précisé"
}

export function DemandesApp() {
  const parametres = useSearchParams()
  const { session, role, chargement: chargementSession } = useSession()
  const estApprobateur = role === ROLE_APPROBATEUR && session.mode === "nominatif"

  const vueInitiale = useMemo<Vue>(() => {
    const demandee = parametres.get("vue")
    if (estApprobateur && (demandee === "a_decider" || demandee === "miennes" || demandee === "toutes")) {
      return demandee
    }
    return estApprobateur ? "a_decider" : "miennes"
  }, [estApprobateur, parametres])

  const [vue, setVue] = useState<Vue>(vueInitiale)
  const [vueInitialisee, setVueInitialisee] = useState(false)
  const [statut, setStatut] = useState("")
  const [typeDemande, setTypeDemande] = useState("")
  const [codeFiche, setCodeFiche] = useState("")
  const [offset, setOffset] = useState(0)
  const [demandes, setDemandes] = useState<DemandeApprobation[]>([])
  const [total, setTotal] = useState(0)
  const [detail, setDetail] = useState<DemandeApprobation | null>(null)
  const idInitial = useMemo(() => {
    const brut = Number(parametres.get("id"))
    return Number.isInteger(brut) && brut > 0 ? brut : null
  }, [parametres])
  const [idSelectionne, setIdSelectionne] = useState<number | null>(idInitial)
  const [chargement, setChargement] = useState(true)
  const [erreur, setErreur] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [decision, setDecision] = useState<"approuver" | "refuser">("approuver")
  const [commentaire, setCommentaire] = useState("")
  const [revisionRelue, setRevisionRelue] = useState(false)
  const [envoiDecision, setEnvoiDecision] = useState(false)
  const [confirmationAnnulation, setConfirmationAnnulation] = useState(false)
  const generation = useRef(0)

  useEffect(() => {
    if (!chargementSession && !vueInitialisee) {
      setVue(vueInitiale)
      setVueInitialisee(true)
    }
  }, [chargementSession, vueInitiale, vueInitialisee])

  const charger = useCallback(
    async (silencieux = false) => {
      if (session.mode !== "nominatif") return
      const jeton = ++generation.current
      if (!silencieux) setChargement(true)
      const params = new URLSearchParams({
        vue,
        limit: String(LIMITE),
        offset: String(offset),
      })
      if (statut) params.set("statut", statut)
      if (typeDemande) params.set("type_demande", typeDemande)
      if (codeFiche.trim()) params.set("code_fiche", codeFiche.trim())
      try {
        const reponse = await chargerDemandes(params)
        if (generation.current !== jeton) return
        setDemandes(reponse.demandes)
        setTotal(reponse.total)
        setErreur(null)
        setIdSelectionne((actuel) => {
          if (actuel && reponse.demandes.some((demande) => demande.id_demande === actuel)) return actuel
          return reponse.demandes[0]?.id_demande ?? actuel
        })
      } catch (error) {
        if (generation.current === jeton) {
          setErreur(error instanceof Error ? error.message : "Les demandes sont indisponibles.")
        }
      } finally {
        if (generation.current === jeton && !silencieux) setChargement(false)
      }
    },
    [codeFiche, offset, session.mode, statut, typeDemande, vue],
  )

  useEffect(() => {
    if (!vueInitialisee) return
    void charger()
  }, [charger, vueInitialisee])

  useEffect(() => {
    if (!vueInitialisee || session.mode !== "nominatif") return
    const timer = window.setInterval(() => void charger(true), 15_000)
    return () => window.clearInterval(timer)
  }, [charger, session.mode, vueInitialisee])

  useEffect(() => {
    if (idSelectionne == null) {
      setDetail(null)
      return
    }
    let actif = true
    chargerDemande(idSelectionne)
      .then((demande) => {
        if (actif) setDetail(demande)
      })
      .catch((error) => {
        if (actif) setErreur(error instanceof Error ? error.message : "Détail de demande indisponible.")
      })
    return () => {
      actif = false
    }
  }, [idSelectionne])

  useEffect(() => {
    setDecision("approuver")
    setCommentaire("")
    setRevisionRelue(false)
    setConfirmationAnnulation(false)
  }, [idSelectionne])

  if (!chargementSession && session.mode !== "nominatif") {
    return (
      <AccesRefuse message="Les demandes et notifications exigent un compte nominatif. Le compte de secours ne peut ni demander ni décider une approbation métier." />
    )
  }

  const estMaDemande = detail?.demandeur === session.identifiant
  const revisionDivergente =
    detail?.type_demande === "modification" &&
    detail.revision_fiche != null &&
    detail.revision_fiche_actuelle != null &&
    detail.revision_fiche !== detail.revision_fiche_actuelle
  const telechargementUtilisable =
    detail?.type_demande === "telechargement" &&
    detail.id_piece != null &&
    detail.statut === "approuvee" &&
    detail.expire_le !== null &&
    new Date(detail.expire_le).getTime() > Date.now()

  async function soumettreDecision(event: React.FormEvent) {
    event.preventDefault()
    if (!detail) return
    setEnvoiDecision(true)
    setErreur(null)
    setMessage(null)
    try {
      const resultat = await deciderDemande(detail.id_demande, {
        decision,
        commentaire: commentaire.trim() || undefined,
        ...(decision === "approuver" && revisionDivergente && revisionRelue && detail.revision_fiche_actuelle != null
          ? { revision_confirmee: detail.revision_fiche_actuelle }
          : {}),
      })
      setDetail(resultat)
      setMessage(
        resultat.statut === "refusee"
          ? "Demande refusée et demandeur notifié."
          : resultat.statut === "executee"
            ? "Demande approuvée et exécutée."
            : "Demande approuvée ; le téléchargement est ouvert pour 24 heures.",
      )
      await charger(true)
    } catch (error) {
      setErreur(error instanceof Error ? error.message : "La décision n’a pas pu être enregistrée.")
    } finally {
      setEnvoiDecision(false)
    }
  }

  async function annuler() {
    if (!detail) return
    setErreur(null)
    setMessage(null)
    try {
      const resultat = await annulerDemande(detail.id_demande)
      setDetail(resultat)
      setMessage("Demande annulée.")
      setConfirmationAnnulation(false)
      await charger(true)
    } catch (error) {
      setErreur(error instanceof Error ? error.message : "L’annulation a échoué.")
    }
  }

  function changerVue(nouvelle: Vue) {
    setVue(nouvelle)
    setOffset(0)
    setIdSelectionne(null)
  }

  return (
    <div className="mx-auto min-h-[calc(100vh-3.5rem)] w-full max-w-[1500px] p-4 sm:p-6" data-testid="approvals-app">
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-border pb-4">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-primary">Circuit d’approbation</p>
          <h1 className="mt-1 flex items-center gap-2 text-xl font-semibold">
            <ClipboardList className="size-5 text-primary" aria-hidden /> Demandes d’approbation
          </h1>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted-foreground">
            Téléchargements, modifications et suppressions logiques sont décidés uniquement par un approbateur.
            Aucune approbation ne modifie silencieusement une donnée déjà relue sous une autre révision.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void charger()}
          className="inline-flex items-center gap-2 rounded border border-border px-3 py-2 text-xs hover:bg-accent"
        >
          <RefreshCw className={cn("size-3.5", chargement && "animate-spin")} aria-hidden /> Actualiser
        </button>
      </header>

      <div className="mt-4 flex flex-wrap gap-2" role="tablist" aria-label="Vues des demandes">
        {estApprobateur && (
          <button
            type="button"
            role="tab"
            aria-selected={vue === "a_decider"}
            onClick={() => changerVue("a_decider")}
            className={cn("rounded px-3 py-2 text-xs font-medium", vue === "a_decider" ? "bg-primary text-primary-foreground" : "bg-accent text-muted-foreground")}
            data-testid="vue-a-decider"
          >
            À décider
          </button>
        )}
        <button
          type="button"
          role="tab"
          aria-selected={vue === "miennes"}
          onClick={() => changerVue("miennes")}
          className={cn("rounded px-3 py-2 text-xs font-medium", vue === "miennes" ? "bg-primary text-primary-foreground" : "bg-accent text-muted-foreground")}
          data-testid="vue-miennes"
        >
          Mes demandes
        </button>
        {estApprobateur && (
          <button
            type="button"
            role="tab"
            aria-selected={vue === "toutes"}
            onClick={() => changerVue("toutes")}
            className={cn("rounded px-3 py-2 text-xs font-medium", vue === "toutes" ? "bg-primary text-primary-foreground" : "bg-accent text-muted-foreground")}
            data-testid="vue-toutes"
          >
            Toutes
          </button>
        )}
      </div>

      <form
        className="mt-3 grid gap-2 sm:grid-cols-4"
        onSubmit={(event) => {
          event.preventDefault()
          setOffset(0)
          void charger()
        }}
      >
        <label className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          Statut
          <select value={statut} onChange={(event) => { setStatut(event.target.value); setOffset(0) }} className="mt-1 w-full rounded border border-border bg-input px-2 py-2 text-xs normal-case tracking-normal text-foreground">
            <option value="">Tous les statuts</option>
            <option value="en_attente">En attente</option>
            <option value="approuvee">Approuvée</option>
            <option value="refusee">Refusée</option>
            <option value="annulee">Annulée</option>
            <option value="executee">Exécutée</option>
            <option value="expiree">Expirée</option>
          </select>
        </label>
        <label className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          Type
          <select value={typeDemande} onChange={(event) => { setTypeDemande(event.target.value); setOffset(0) }} className="mt-1 w-full rounded border border-border bg-input px-2 py-2 text-xs normal-case tracking-normal text-foreground">
            <option value="">Tous les types</option>
            <option value="telechargement">Téléchargement</option>
            <option value="modification">Modification</option>
            <option value="suppression">Suppression logique</option>
          </select>
        </label>
        <label className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground sm:col-span-2">
          Code fiche
          <input value={codeFiche} onChange={(event) => setCodeFiche(event.target.value)} placeholder="Ex. 1001-GV-006" className="mt-1 w-full rounded border border-border bg-input px-2 py-2 text-xs normal-case tracking-normal text-foreground" />
        </label>
      </form>

      {message && <p className="mt-4 rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200" role="status">{message}</p>}
      {erreur && <p className="mt-4 rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive" role="alert">{erreur}</p>}

      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(20rem,0.85fr)_minmax(0,1.15fr)]">
        <section className="overflow-hidden rounded-xl border border-border bg-card" aria-label="Liste des demandes">
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Demandes</h2>
            <span className="text-[10px] text-muted-foreground">{total} au total</span>
          </div>
          <div data-testid="approvals-list">
            {chargement && demandes.length === 0 && <p className="p-6 text-sm text-muted-foreground">Chargement des demandes…</p>}
            {!chargement && demandes.length === 0 && <p className="p-6 text-sm text-muted-foreground">Aucune demande dans cette vue.</p>}
            <ul className="divide-y divide-border/60">
              {demandes.map((demande) => (
                <li key={demande.id_demande}>
                  <button
                    type="button"
                    onClick={() => setIdSelectionne(demande.id_demande)}
                    className={cn("w-full px-4 py-3 text-left transition-colors hover:bg-accent/50", detail?.id_demande === demande.id_demande && "bg-primary/10")}
                    data-testid={`approval-${demande.id_demande}`}
                  >
                    <span className="flex items-start justify-between gap-3">
                      <span>
                        <span className="block text-xs font-semibold">{typeDemandeLibelle(demande.type_demande)} · {objetDemande(demande)}</span>
                        <span className="mt-1 block text-[10px] text-muted-foreground">
                          #{demande.id_demande} · {demande.demandeur} · {dateLisible(demande.cree_le)}
                        </span>
                      </span>
                      <span className={cn("shrink-0 rounded-full border px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wide", statutClasses(demande.statut))}>
                        {statutDemandeLibelle(demande.statut)}
                      </span>
                    </span>
                    <span className="mt-2 line-clamp-2 block text-[11px] leading-relaxed text-muted-foreground">{demande.motif}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <footer className="flex items-center justify-between border-t border-border px-4 py-3">
            <button type="button" disabled={offset === 0} onClick={() => setOffset((value) => Math.max(0, value - LIMITE))} className="rounded border border-border px-2.5 py-1.5 text-xs disabled:opacity-40">Précédent</button>
            <span className="text-[10px] text-muted-foreground">Page {Math.floor(offset / LIMITE) + 1}</span>
            <button type="button" disabled={offset + LIMITE >= total} onClick={() => setOffset((value) => value + LIMITE)} className="rounded border border-border px-2.5 py-1.5 text-xs disabled:opacity-40">Suivant</button>
          </footer>
        </section>

        <section className="rounded-xl border border-border bg-card p-5" aria-label="Détail de la demande" data-testid="approval-detail">
          {!detail && <p className="text-sm text-muted-foreground">Sélectionnez une demande pour voir son détail et son historique de décision.</p>}
          {detail && (
            <>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-primary">Demande #{detail.id_demande}</p>
                  <h2 className="mt-1 text-lg font-semibold">{typeDemandeLibelle(detail.type_demande)} · {objetDemande(detail)}</h2>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Demandée par {detail.nom_demandeur ? `${detail.nom_demandeur} (${detail.demandeur})` : detail.demandeur} le {dateLisible(detail.cree_le)}
                  </p>
                </div>
                <span className={cn("rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide", statutClasses(detail.statut))}>
                  {statutDemandeLibelle(detail.statut)}
                </span>
              </div>

              <dl className="mt-5 grid gap-3 text-xs sm:grid-cols-2">
                <div><dt className="text-muted-foreground">Fiche</dt><dd className="mt-0.5 font-medium">{detail.code_fiche ? <Link className="text-primary hover:underline" href={`/fiches/${encodeURIComponent(detail.code_fiche)}`}>{detail.code_fiche}</Link> : "—"}</dd></div>
                <div><dt className="text-muted-foreground">Pièce</dt><dd className="mt-0.5 font-medium">{detail.nom_piece ?? (detail.id_piece ? `#${detail.id_piece}` : "—")}</dd></div>
                <div><dt className="text-muted-foreground">Décideur</dt><dd className="mt-0.5 font-medium">{detail.decideur ?? "—"}</dd></div>
                <div><dt className="text-muted-foreground">Décidée le</dt><dd className="mt-0.5 font-medium">{dateLisible(detail.decidee_le)}</dd></div>
                <div><dt className="text-muted-foreground">Exécutée le</dt><dd className="mt-0.5 font-medium">{dateLisible(detail.executee_le)}</dd></div>
                <div><dt className="text-muted-foreground">Expire le</dt><dd className="mt-0.5 font-medium">{dateLisible(detail.expire_le)}</dd></div>
              </dl>

              <div className="mt-5">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Motif du demandeur</h3>
                <p className="mt-1 whitespace-pre-wrap rounded border border-border bg-background/50 p-3 text-xs leading-relaxed">{detail.motif}</p>
              </div>

              {detail.type_demande === "modification" && (
                <div className="mt-5 rounded border border-border bg-background/50 p-3 text-xs">
                  <h3 className="font-semibold">Modification proposée</h3>
                  <dl className="mt-2 grid gap-2 sm:grid-cols-2">
                    <div><dt className="text-muted-foreground">Champ</dt><dd className="mt-0.5 font-mono">{detail.champ}{detail.rang != null ? ` [${detail.rang}]` : ""}</dd></div>
                    <div><dt className="text-muted-foreground">Nouvelle valeur</dt><dd className="mt-0.5 font-medium">{detail.valeur_proposee || "Valeur vide"}</dd></div>
                    <div><dt className="text-muted-foreground">Révision relue</dt><dd className="mt-0.5">{detail.revision_fiche ?? "—"}</dd></div>
                    <div><dt className="text-muted-foreground">Révision actuelle</dt><dd className="mt-0.5">{detail.revision_fiche_actuelle ?? "—"}</dd></div>
                  </dl>
                  {revisionDivergente && (
                    <p className="badge-attention mt-3 rounded p-2" role="alert">
                      La fiche a changé depuis la proposition. Relisez sa valeur actuelle avant de confirmer explicitement la nouvelle révision.
                    </p>
                  )}
                </div>
              )}

              {detail.type_demande === "suppression" && (
                <p className="mt-5 rounded border border-red-500/40 bg-red-500/10 p-3 text-xs leading-relaxed text-red-100">
                  Suppression logique uniquement : la fiche sortira de la recherche et de la validation ; les fichiers sources de l’archive ne seront pas effacés.
                </p>
              )}

              {telechargementUtilisable && detail.id_piece != null && (
                <div className="mt-5">
                  <PieceDownload
                    piece={{ id: detail.id_piece, name: detail.nom_piece ?? `piece-${detail.id_piece}`, fiche_code: detail.code_fiche ?? undefined }}
                    codeFiche={detail.code_fiche ?? undefined}
                    className="inline-flex rounded bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground"
                    label="Télécharger le fichier approuvé"
                  />
                </div>
              )}

              {detail.commentaire_decision && (
                <div className="mt-5">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Commentaire de décision</h3>
                  <p className="mt-1 whitespace-pre-wrap rounded border border-border bg-background/50 p-3 text-xs leading-relaxed">{detail.commentaire_decision}</p>
                </div>
              )}

              {detail.resultat_execution && Object.keys(detail.resultat_execution).length > 0 && (
                <details className="mt-5 text-xs">
                  <summary className="cursor-pointer font-medium">Résultat d’exécution</summary>
                  <pre className="mt-2 max-h-64 overflow-auto rounded border border-border bg-background/60 p-3 font-mono text-[10px]">{JSON.stringify(detail.resultat_execution, null, 2)}</pre>
                </details>
              )}

              {estMaDemande && detail.statut === "en_attente" && (
                <div className="mt-6 border-t border-border pt-4">
                  {!confirmationAnnulation ? (
                    <button type="button" onClick={() => setConfirmationAnnulation(true)} className="rounded border border-border px-3 py-2 text-xs hover:bg-accent">
                      Annuler ma demande
                    </button>
                  ) : (
                    <div className="flex flex-wrap items-center gap-2" role="alert">
                      <span className="text-xs">Confirmer l’annulation de cette demande ?</span>
                      <button type="button" onClick={() => void annuler()} className="rounded bg-destructive px-3 py-1.5 text-xs font-semibold text-white">Confirmer l’annulation</button>
                      <button type="button" onClick={() => setConfirmationAnnulation(false)} className="rounded border border-border px-3 py-1.5 text-xs">Retour</button>
                    </div>
                  )}
                </div>
              )}

              {estApprobateur && detail.statut === "en_attente" && (
                <form onSubmit={soumettreDecision} className="mt-6 space-y-3 border-t border-border pt-5" data-testid="decision-form">
                  <h3 className="text-sm font-semibold">Décider cette demande</h3>
                  {estMaDemande && (
                    <p className="badge-attention rounded p-3 text-xs leading-relaxed" role="alert">
                      Vous êtes le demandeur. Par défaut, l’auto-approbation est interdite : le serveur refusera votre décision et un autre approbateur devra trancher.
                    </p>
                  )}
                  <fieldset>
                    <legend className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Décision</legend>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => setDecision("approuver")}
                        aria-pressed={decision === "approuver"}
                        className={cn("inline-flex items-center gap-1.5 rounded px-3 py-2 text-xs font-semibold", decision === "approuver" ? "bg-emerald-700 text-white" : "bg-accent text-muted-foreground")}
                      >
                        <Check className="size-3.5" aria-hidden /> Approuver
                      </button>
                      <button
                        type="button"
                        onClick={() => setDecision("refuser")}
                        aria-pressed={decision === "refuser"}
                        className={cn("inline-flex items-center gap-1.5 rounded px-3 py-2 text-xs font-semibold", decision === "refuser" ? "bg-red-700 text-white" : "bg-accent text-muted-foreground")}
                      >
                        <X className="size-3.5" aria-hidden /> Refuser
                      </button>
                    </div>
                  </fieldset>

                  {decision === "approuver" && revisionDivergente && (
                    <label className="flex items-start gap-2 text-xs leading-relaxed text-muted-foreground">
                      <input type="checkbox" checked={revisionRelue} onChange={(event) => setRevisionRelue(event.target.checked)} className="mt-0.5" />
                      Je confirme avoir relu la fiche à la révision {detail.revision_fiche_actuelle} et appliquer la proposition à cet état actuel.
                    </label>
                  )}

                  <label className="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                    Commentaire de décision {decision === "refuser" ? "(obligatoire)" : "(facultatif)"}
                    <textarea
                      value={commentaire}
                      onChange={(event) => setCommentaire(event.target.value)}
                      required={decision === "refuser"}
                      maxLength={1000}
                      rows={3}
                      className="mt-1 w-full rounded border border-border bg-input px-2 py-2 text-xs normal-case tracking-normal text-foreground"
                      placeholder={decision === "refuser" ? "Expliquez le refus au demandeur…" : "Précision pour le demandeur…"}
                    />
                  </label>

                  <button
                    type="submit"
                    disabled={envoiDecision || (decision === "refuser" && !commentaire.trim()) || (decision === "approuver" && revisionDivergente && !revisionRelue)}
                    className="rounded bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground disabled:opacity-50"
                  >
                    {envoiDecision ? "Enregistrement…" : "Enregistrer la décision"}
                  </button>
                </form>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  )
}
