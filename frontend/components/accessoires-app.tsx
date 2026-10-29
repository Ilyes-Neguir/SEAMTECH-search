"use client"

// Écran ACCESSOIRES (Lot 7) — pendant simplifié de l'écran voiles (Lot 3b) :
// file des extractions d'accessoires, valeurs métier avec leur provenance
// (valeur brute, page, confiance), panneau modèle de bateau à UN modèle
// (liaison exacte ou décision approbateur, jamais de fusion), corrections
// historisées avec motif. Réservé à un approbateur nominatif, comme les
// voiles ; le backend réapplique les mêmes rôles.

import { useCallback, useEffect, useState } from "react"
import { Link2, RefreshCw, Wand2 } from "lucide-react"
import { AccesRefuse } from "@/components/acces-refuse"
import { authedFetch } from "@/lib/authed-fetch"
import { estApprobateur } from "@/lib/roles"
import { useSession } from "@/lib/use-session"
import { cn } from "@/lib/utils"

interface ChampProvenance {
  nom: string
  etat: string
  valeur_brute: string | null
  valeur_texte: string | null
  unite_brute: string | null
  confiance: number | null
  page: number | null
}

interface Anomalie {
  code: string
  gravite: string
  message: string
}

interface Accessoire {
  id_extraction: number
  code_fiche: string | null
  id_fiche: number | null
  nom_fichier_source: string | null
  type_accessoire: string | null
  designation_brute: string | null
  matiere: string | null
  couleur: string | null
  dimensions_texte: string | null
  longueur_m: number | null
  largeur_m: number | null
  hauteur_m: number | null
  quantite: number | null
  modele_bateau_brut: string | null
  champs: ChampProvenance[]
  anomalies: Anomalie[]
  score_qualite: number | null
  confiance_moyenne: number | null
  statut_extraction: string
  statut_plausibilite: string
  boat_model_id: number | null
  statut_liaison: string
  methode_liaison: string | null
  nom_modele_lie: string | null
}

interface Candidat {
  id_modele: number
  nom_canonique?: string
  nom_affiche?: string
}

interface Correction {
  champ: string
  valeur_avant: unknown
  valeur_apres: unknown
  motif: string | null
  corrige_le: string | null
}

interface DetailAccessoire extends Accessoire {
  candidats: Candidat[]
  corrections: Correction[]
  modele_lie: { id_modele: number; nom_affiche: string | null } | null
}

// Champ corrigible → (libellé, clé de lecture, numérique ?). Les noms de
// champs sont ceux du contrat backend (accessoires.corriger_accessoire).
const CHAMPS_AFFICHES: {
  champ: string
  libelle: string
  lecture: keyof Accessoire
  nombre?: boolean
}[] = [
  { champ: "type_accessoire", libelle: "Type d’accessoire", lecture: "type_accessoire" },
  { champ: "matiere", libelle: "Matière", lecture: "matiere" },
  { champ: "couleur", libelle: "Couleur", lecture: "couleur" },
  { champ: "dimensions", libelle: "Dimensions (texte lu)", lecture: "dimensions_texte" },
  { champ: "longueur_m", libelle: "Longueur (m)", lecture: "longueur_m", nombre: true },
  { champ: "largeur_m", libelle: "Largeur (m)", lecture: "largeur_m", nombre: true },
  { champ: "hauteur_m", libelle: "Hauteur (m)", lecture: "hauteur_m", nombre: true },
  { champ: "quantite", libelle: "Quantité", lecture: "quantite", nombre: true },
  { champ: "modele_bateau_brut", libelle: "Modèle de bateau (lu)", lecture: "modele_bateau_brut" },
]

const LIBELLES_LIAISON: Record<string, string> = {
  non_resolu: "Non résolu",
  lie_automatique: "Lié (preuve exacte)",
  lie_humain: "Lié par un approbateur",
  ambigu: "Ambigu — décision requise",
  sans_modele: "Sans modèle lu",
}

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await authedFetch(url, init)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = (body as { detail?: unknown })?.detail
    throw new Error(typeof detail === "string" ? detail : `HTTP ${res.status}`)
  }
  return body as T
}

function valeurTexte(valeur: unknown): string {
  return valeur === null || valeur === undefined ? "" : String(valeur)
}

function BadgeLiaison({ statut }: { statut: string }) {
  return (
    <span
      className={cn(
        "inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold",
        statut === "lie_automatique" && "bg-emerald-500/20 text-emerald-800 dark:text-emerald-200",
        statut === "lie_humain" && "bg-violet-500/20 text-violet-800 dark:text-violet-200",
        statut === "ambigu" && "badge-attention",
        (statut === "non_resolu" || statut === "sans_modele") && "bg-muted text-muted-foreground",
      )}
      data-testid="statut-liaison"
      data-statut={statut}
    >
      {LIBELLES_LIAISON[statut] ?? statut}
    </span>
  )
}

function AccessoiresAppInterne() {
  const [file, setFile] = useState<Accessoire[]>([])
  const [filtreStatut, setFiltreStatut] = useState("")
  const [recherche, setRecherche] = useState("")
  const [idActif, setIdActif] = useState<number | null>(null)
  const [detail, setDetail] = useState<DetailAccessoire | null>(null)
  const [brouillons, setBrouillons] = useState<Record<string, string>>({})
  const [motif, setMotif] = useState("")
  const [nouveauModele, setNouveauModele] = useState("")
  const [erreur, setErreur] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [occupe, setOccupe] = useState(false)

  const chargerFile = useCallback(async () => {
    const parametres = new URLSearchParams({ limite: "100" })
    if (filtreStatut) parametres.set("statut_liaison", filtreStatut)
    if (recherche.trim()) parametres.set("recherche", recherche.trim())
    try {
      const corps = await jsonFetch<{ extractions: Accessoire[]; total: number }>(
        `/api/accessoires/extractions?${parametres.toString()}`,
      )
      setFile(corps.extractions)
      setIdActif((actuel) => actuel ?? corps.extractions[0]?.id_extraction ?? null)
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "Chargement impossible.")
    }
  }, [filtreStatut, recherche])

  const chargerDetail = useCallback(async (id: number) => {
    try {
      const corps = await jsonFetch<DetailAccessoire>(`/api/accessoires/extractions/${id}`)
      setDetail(corps)
      setBrouillons({})
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "Détail illisible.")
    }
  }, [])

  useEffect(() => {
    void chargerFile()
  }, [chargerFile])

  useEffect(() => {
    if (idActif !== null) void chargerDetail(idActif)
    else setDetail(null)
  }, [idActif, chargerDetail])

  async function apresMutation(texte: string) {
    setMessage(texte)
    setErreur(null)
    await chargerFile()
    if (idActif !== null) await chargerDetail(idActif)
  }

  async function resoudre() {
    if (idActif === null) return
    setOccupe(true)
    try {
      const decision = await jsonFetch<{ statut_liaison: string }>(
        `/api/accessoires/extractions/${idActif}/resoudre`,
        { method: "POST", body: JSON.stringify({}) },
      )
      await apresMutation(`Résolution automatique : ${LIBELLES_LIAISON[decision.statut_liaison] ?? decision.statut_liaison}.`)
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "Résolution refusée.")
    } finally {
      setOccupe(false)
    }
  }

  async function lier(corps: Record<string, unknown>) {
    if (idActif === null) return
    setOccupe(true)
    try {
      await jsonFetch(`/api/accessoires/extractions/${idActif}/lier`, {
        method: "POST",
        body: JSON.stringify(corps),
      })
      setNouveauModele("")
      await apresMutation("Modèle de bateau lié par votre décision.")
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "Liaison refusée.")
    } finally {
      setOccupe(false)
    }
  }

  async function corriger(champ: string, valeurBrute: string, nombre: boolean) {
    if (idActif === null || !detail) return
    let valeur: unknown = valeurBrute.trim()
    if (nombre) {
      const lu = Number(String(valeur).replace(",", "."))
      if (!Number.isFinite(lu)) {
        setErreur("Valeur numérique attendue pour ce champ.")
        return
      }
      valeur = champ === "quantite" ? Math.trunc(lu) : lu
    }
    if (valeur === "") valeur = null
    setOccupe(true)
    try {
      await jsonFetch(`/api/accessoires/extractions/${idActif}/corriger`, {
        method: "POST",
        body: JSON.stringify({ champ, valeur, motif: motif || undefined }),
      })
      await apresMutation("Correction enregistrée (historisée).")
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "Correction refusée.")
    } finally {
      setOccupe(false)
    }
  }

  const extraction = detail

  return (
    <div className="flex min-h-[calc(100vh-3.5rem)] flex-col">
      {(erreur || message) && (
        <div
          className={cn(
            "border-b px-3 py-2 text-xs",
            erreur
              ? "border-destructive/40 bg-destructive/10 text-destructive"
              : "border-emerald-500/40 bg-emerald-500/10",
          )}
          role={erreur ? "alert" : "status"}
          data-testid={erreur ? "erreur-accessoire" : "message-accessoire"}
        >
          {erreur ?? message}
        </div>
      )}
      <div className="grid min-h-0 flex-1 grid-cols-[20rem_minmax(0,1fr)]">
        <aside className="flex min-h-0 flex-col border-r border-border">
          <header className="flex items-center justify-between border-b border-border p-3">
            <h2 className="text-sm font-semibold">Extractions d’accessoires</h2>
            <button type="button" onClick={() => void chargerFile()} className="text-muted-foreground hover:text-foreground" aria-label="Rafraîchir">
              <RefreshCw className="size-4" />
            </button>
          </header>
          <div className="space-y-2 border-b border-border p-3">
            <select
              value={filtreStatut}
              onChange={(e) => setFiltreStatut(e.target.value)}
              className="w-full rounded border border-border bg-input px-2 py-1 text-xs"
              data-testid="filtre-statut-liaison-accessoires"
              aria-label="Filtrer par statut de liaison"
            >
              <option value="">Tous les statuts de liaison</option>
              <option value="non_resolu">Non résolues</option>
              <option value="ambigu">Ambiguës (décision requise)</option>
              <option value="lie_automatique">Liées automatiquement</option>
              <option value="lie_humain">Liées par un approbateur</option>
              <option value="sans_modele">Sans modèle lu</option>
            </select>
            <input
              value={recherche}
              onChange={(e) => setRecherche(e.target.value)}
              placeholder="Code, type, matière, bateau…"
              className="w-full rounded border border-border bg-input px-2 py-1 text-xs"
              data-testid="recherche-accessoires"
            />
          </div>
          <ul className="min-h-0 flex-1 overflow-auto" data-testid="file-accessoires">
            {file.length === 0 && (
              <li className="p-3 text-xs text-muted-foreground">Aucun accessoire pour ce filtre.</li>
            )}
            {file.map((entree) => (
              <li key={entree.id_extraction}>
                <button
                  type="button"
                  onClick={() => setIdActif(entree.id_extraction)}
                  className={cn(
                    "block w-full border-b border-border/60 px-3 py-2 text-left text-xs hover:bg-accent/40",
                    idActif === entree.id_extraction && "bg-accent",
                  )}
                  data-testid="accessoire-item"
                  data-id={entree.id_extraction}
                >
                  <span className="block truncate font-mono font-semibold">
                    {entree.code_fiche ?? `#${entree.id_extraction}`}
                  </span>
                  <span className="block truncate text-muted-foreground">
                    {entree.type_accessoire ?? "Type non lu"} · {entree.modele_bateau_brut ?? "bateau non lu"}
                  </span>
                  <span className="mt-1 flex flex-wrap gap-1">
                    <BadgeLiaison statut={entree.statut_liaison} />
                    {entree.statut_plausibilite !== "plausible" && (
                      <span className="inline-block badge-attention rounded px-1.5 py-0.5 text-[10px]">
                        {entree.statut_plausibilite}
                      </span>
                    )}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <section className="min-h-0 overflow-auto" data-testid="detail-accessoire">
          {!extraction && (
            <p className="p-4 text-sm text-muted-foreground">
              Sélectionnez un accessoire : ses valeurs lues (avec provenance) et son modèle de
              bateau se décident ici. Un accessoire ne retient qu’un seul modèle — rien n’est lié
              ni corrigé sans votre décision.
            </p>
          )}
          {extraction && (
            <div className="space-y-4 p-3">
              <header>
                <h2 className="font-mono text-sm font-semibold" data-testid="code-accessoire">
                  {extraction.code_fiche ?? `#${extraction.id_extraction}`}
                </h2>
                <p className="text-xs text-muted-foreground">
                  {extraction.nom_fichier_source ?? "source inconnue"} · extraction{" "}
                  {extraction.statut_extraction} · plausibilité {extraction.statut_plausibilite}
                  {extraction.confiance_moyenne !== null &&
                    ` · confiance moyenne ${Math.round(extraction.confiance_moyenne * 100)} %`}
                </p>
              </header>

              {extraction.anomalies.length > 0 && (
                <ul className="space-y-1" data-testid="anomalies-accessoire">
                  {extraction.anomalies.map((a) => (
                    <li key={a.code} className="rounded bg-amber-500/10 px-2 py-1 text-xs">
                      <span className="font-semibold">{a.code}</span> — {a.message}
                    </li>
                  ))}
                </ul>
              )}

              <section className="rounded border border-border p-3" data-testid="panneau-modele-accessoire">
                <h3 className="flex items-center gap-1 text-xs font-semibold">
                  <Link2 className="size-3.5" /> Modèle de bateau (un seul)
                </h3>
                <p className="mt-1 text-xs">
                  Lu : <span className="font-mono">{extraction.modele_bateau_brut ?? "—"}</span>{" "}
                  <BadgeLiaison statut={extraction.statut_liaison} />
                </p>
                {extraction.modele_lie && (
                  <p className="mt-1 text-xs" data-testid="modele-lie-accessoire">
                    Lié à <span className="font-semibold">{extraction.modele_lie.nom_affiche}</span>
                    {extraction.methode_liaison && ` (${extraction.methode_liaison})`}
                  </p>
                )}
                {!extraction.modele_lie && extraction.candidats.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {extraction.candidats.map((c) => (
                      <button
                        key={c.id_modele}
                        type="button"
                        disabled={occupe}
                        onClick={() => void lier({ id_modele: c.id_modele })}
                        className="rounded border border-border px-2 py-1 text-xs font-semibold disabled:opacity-40"
                        data-testid="lier-candidat-accessoire"
                      >
                        Lier à {c.nom_affiche ?? c.nom_canonique ?? `#${c.id_modele}`}
                      </button>
                    ))}
                  </div>
                )}
                {!extraction.modele_lie && (
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      disabled={occupe}
                      onClick={() => void resoudre()}
                      className="flex items-center gap-1 rounded border border-border px-2 py-1 text-xs font-semibold disabled:opacity-40"
                      data-testid="bouton-resoudre-accessoire"
                    >
                      <Wand2 className="size-3.5" /> Résoudre (preuves exactes)
                    </button>
                    <input
                      value={nouveauModele}
                      onChange={(e) => setNouveauModele(e.target.value)}
                      placeholder="Nouveau modèle (ex. Bavaria 45)"
                      className="rounded border border-border bg-input px-2 py-1 text-xs"
                      data-testid="nouveau-modele-accessoire"
                    />
                    <button
                      type="button"
                      disabled={occupe || !nouveauModele.trim()}
                      onClick={() => void lier({ nouveau_modele: nouveauModele.trim() })}
                      className="rounded border border-border px-2 py-1 text-xs font-semibold disabled:opacity-40"
                      data-testid="bouton-creer-modele-accessoire"
                    >
                      Créer et lier
                    </button>
                  </div>
                )}
              </section>

              <section>
                <h3 className="text-xs font-semibold">Valeurs de l’accessoire</h3>
                <div className="mt-2 space-y-2">
                  {CHAMPS_AFFICHES.map((info) => {
                    const actuelle = valeurTexte(extraction[info.lecture])
                    const brouillon = brouillons[info.champ] ?? actuelle
                    const modifie = brouillon !== actuelle
                    const provenance = extraction.champs.find(
                      (c) => c.nom === info.lecture || c.nom === info.champ,
                    )
                    return (
                      <div key={info.champ} className="flex flex-wrap items-center gap-2 text-xs">
                        <label className="w-44 text-muted-foreground">{info.libelle}</label>
                        <input
                          value={brouillon}
                          onChange={(e) =>
                            setBrouillons((b) => ({ ...b, [info.champ]: e.target.value }))
                          }
                          className="w-56 rounded border border-border bg-input px-2 py-1"
                          data-testid={`champ-accessoire-${info.champ}`}
                        />
                        <button
                          type="button"
                          disabled={!modifie || occupe}
                          onClick={() => void corriger(info.champ, brouillon, Boolean(info.nombre))}
                          className="rounded border border-border px-2 py-1 font-semibold disabled:opacity-40"
                          data-testid={`corriger-accessoire-${info.champ}`}
                        >
                          Corriger
                        </button>
                        {provenance && (
                          <span className="text-muted-foreground">
                            lu : « {provenance.valeur_brute ?? "—"} »
                            {provenance.page ? ` · p. ${provenance.page}` : ""}
                            {provenance.confiance !== null
                              ? ` · ${Math.round(provenance.confiance * 100)} %`
                              : ""}
                          </span>
                        )}
                      </div>
                    )
                  })}
                </div>
                <div className="mt-2 flex items-center gap-2 text-xs">
                  <label className="w-44 text-muted-foreground">Motif des corrections</label>
                  <input
                    value={motif}
                    onChange={(e) => setMotif(e.target.value)}
                    placeholder="Ex. relu sur la fiche papier"
                    className="w-56 rounded border border-border bg-input px-2 py-1"
                    data-testid="motif-accessoire"
                  />
                </div>
              </section>

              {extraction.corrections.length > 0 && (
                <section data-testid="historique-corrections-accessoire">
                  <h3 className="text-xs font-semibold">Corrections historisées</h3>
                  <ul className="mt-1 space-y-1 text-xs text-muted-foreground">
                    {extraction.corrections.map((c, i) => (
                      <li key={`${c.champ}-${i}`}>
                        <span className="font-semibold text-foreground">{c.champ}</span> :{" "}
                        {valeurTexte(c.valeur_avant) || "∅"} → {valeurTexte(c.valeur_apres) || "∅"}
                        {c.motif && ` — ${c.motif}`}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

export function AccessoiresApp() {
  const { session, role, chargement } = useSession()
  if (chargement) {
    return <div className="p-6 text-sm text-muted-foreground">Vérification de votre rôle…</div>
  }
  if (session.mode !== "nominatif" || !estApprobateur(role)) {
    return (
      <AccesRefuse message="La liaison des modèles de bateau et la correction des accessoires sont réservées à un approbateur nominatif. Les autres comptes consultent les fiches et utilisent l’écran de recherche." />
    )
  }
  return <AccessoiresAppInterne />
}
