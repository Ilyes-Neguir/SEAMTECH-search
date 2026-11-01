"use client"

// Écran EXTRACTIONS (Lot 3b) — correction côte à côte par l'approbateur :
// file des extractions de voiles, valeurs canoniques avec leur provenance
// (valeur brute, confiance, page) et le PDF source à droite avec surlignage
// de zone. Le panneau « Modèle de bateau » résout le libellé brut vers le
// référentiel canonique : liaison automatique seulement sur preuve exacte,
// sinon l'approbateur rattache à un candidat ou crée le modèle — la décision
// apprend un alias pour les extractions suivantes. Aucune fusion silencieuse
// (« 45 » ne devient jamais « 45.1 » tout seul).

import { useCallback, useEffect, useRef, useState } from "react"
import { Crosshair, Link2, RefreshCw, Sparkles, Wand2 } from "lucide-react"
import { AccesRefuse } from "@/components/acces-refuse"
import { VisionneusePiece, type ZoneASurligner } from "@/components/visionneuse-piece"
import { authedFetch } from "@/lib/authed-fetch"
import type { PieceJointe } from "@/lib/fiche"
import { estApprobateur } from "@/lib/roles"
import { useSession } from "@/lib/use-session"
import { cn } from "@/lib/utils"

interface Zone {
  page: number
  x0: number
  y0: number
  x1: number
  y1: number
}

interface ChampProvenance {
  champ: string
  valeur_brute: string | null
  valeur_normalisee: string | null
  confiance: number | null
  page: number | null
  zone: Zone | null
  colonne_cible: string | null
  etat: string
}

interface Anomalie {
  code: string
  gravite: string
  message: string
}

interface ModeleLie {
  id_modele: number
  nom_canonique: string
  cle_normalisee: string
}

interface Extraction {
  id_extraction: number
  code_fiche: string | null
  id_fiche: number | null
  id_document: number | null
  nom_fichier_source: string | null
  gabarit_code: string | null
  type_voile: string | null
  gamme: string | null
  modele_bateau_brut: string | null
  taille_bateau_brute: string | null
  slu_m: number | null
  sle_m: number | null
  sf_m: number | null
  shw_m: number | null
  spa_m2: number | null
  tetiere_m: number | null
  poids_kg: number | null
  champs: ChampProvenance[]
  anomalies: Anomalie[]
  score_qualite: number | null
  statut_extraction: string
  statut_plausibilite: string
  boat_model_id: number | null
  statut_liaison: string
  methode_liaison: string | null
  modele_lie?: ModeleLie | null
}

interface Candidat {
  id_modele: number
  nom_canonique: string
  type_correspondance: string
  score: number
  alias: string | null
}

interface Correction {
  id_correction: number
  champ: string
  valeur_avant: unknown
  valeur_apres: unknown
  corrige_par: string | null
  motif: string | null
  propagation: string
  cree_le: string | null
}

interface SuggestionMotif {
  champ: string
  valeur_corrigee: string
  occurrences: number
}

interface DetailExtraction {
  extraction: Extraction
  modele_lie: ModeleLie | null
  candidats: Candidat[]
  corrections: Correction[]
  suggestions_motifs: SuggestionMotif[]
  fiche: { id_fiche: number; code: string; statut: string; revision: number } | null
}

const CHAMPS_AFFICHES: { nom: keyof Extraction & string; libelle: string; nombre: boolean }[] = [
  { nom: "type_voile", libelle: "Type de voile", nombre: false },
  { nom: "gamme", libelle: "Gamme", nombre: false },
  { nom: "modele_bateau_brut", libelle: "Modèle de bateau (brut)", nombre: false },
  { nom: "taille_bateau_brute", libelle: "Taille du bateau (brute)", nombre: false },
  { nom: "slu_m", libelle: "SLU (m)", nombre: true },
  { nom: "sle_m", libelle: "SLE (m)", nombre: true },
  { nom: "sf_m", libelle: "SF (m)", nombre: true },
  { nom: "shw_m", libelle: "SHW (m)", nombre: true },
  { nom: "spa_m2", libelle: "SPA (m²)", nombre: true },
  { nom: "tetiere_m", libelle: "Têtière (m)", nombre: true },
  { nom: "poids_kg", libelle: "Poids (kg)", nombre: true },
]

const LIBELLES_LIAISON: Record<string, string> = {
  non_resolu: "Non résolu",
  lie_automatique: "Lié (automatique)",
  lie_humain: "Lié (approbateur)",
  ambigu: "Ambigu — décision requise",
  sans_modele: "Sans modèle lu",
}

const LIBELLES_METHODE: Record<string, string> = {
  cle_exacte: "clé exacte",
  alias_exact: "alias appris",
  fiche_bateau: "bateau de la fiche",
  humain: "décision approbateur",
  humain_nouveau_modele: "modèle créé par l’approbateur",
}

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await authedFetch(url, init)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = body?.detail
    const message =
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object" && "message" in detail
          ? String(detail.message)
          : `HTTP ${res.status}`
    throw Object.assign(new Error(message), { status: res.status, body })
  }
  return body as T
}

function valeurTexte(valeur: unknown): string {
  if (valeur === null || valeur === undefined) return ""
  return String(valeur)
}

function provenancePour(extraction: Extraction, champ: string): ChampProvenance | undefined {
  if (champ.endsWith("_m") || champ.endsWith("_m2") || champ.endsWith("_kg")) {
    const colonne = champ === "tetiere_m" ? "tetiere_cm" : champ
    return extraction.champs.find((c) => c.colonne_cible === colonne)
  }
  if (champ === "modele_bateau_brut" || champ === "taille_bateau_brute") {
    return extraction.champs.find((c) => c.champ === "fiche.bateau")
  }
  if (champ === "type_voile" || champ === "gamme") {
    return extraction.champs.find((c) => c.champ === "fiche.designation")
  }
  return undefined
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

function ExtractionsAppInterne() {
  const [file, setFile] = useState<Extraction[]>([])
  const [filtreStatut, setFiltreStatut] = useState("")
  const [recherche, setRecherche] = useState("")
  const [idActif, setIdActif] = useState<number | null>(null)
  const [detail, setDetail] = useState<DetailExtraction | null>(null)
  const [brouillons, setBrouillons] = useState<Record<string, string>>({})
  const [motif, setMotif] = useState("")
  const [nouveauModele, setNouveauModele] = useState("")
  const [zone, setZone] = useState<ZoneASurligner | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [occupe, setOccupe] = useState(false)
  const [piecePdf, setPiecePdf] = useState<{ idExtraction: number; piece: PieceJointe | null } | null>(null)
  const generation = useRef(0)

  const chargerFile = useCallback(async () => {
    const parametres = new URLSearchParams({ limite: "100" })
    if (filtreStatut) parametres.set("statut_liaison", filtreStatut)
    if (recherche.trim()) parametres.set("recherche", recherche.trim())
    try {
      const corps = await jsonFetch<{ extractions: Extraction[]; total: number }>(
        `/api/extractions-voile?${parametres.toString()}`,
      )
      setFile(corps.extractions)
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "File des extractions illisible.")
    }
  }, [filtreStatut, recherche])

  const chargerDetail = useCallback(async (idExtraction: number) => {
    const jeton = ++generation.current
    setZone(null)
    setPiecePdf(null)
    try {
      const corps = await jsonFetch<DetailExtraction>(`/api/extractions-voile/${idExtraction}`)
      if (jeton !== generation.current) return
      setDetail(corps)
      setBrouillons({})
      setNouveauModele(corps.extraction.modele_bateau_brut ?? "")
      setPiecePdf({
        idExtraction,
        piece: corps.extraction.id_document
          ? {
              id: corps.extraction.id_document,
              name: corps.extraction.nom_fichier_source ?? "fiche-source.pdf",
              extension: ".pdf",
              size: null,
              kind: "pdf",
              is_primary_pdf: true,
              previewable: true,
            }
          : null,
      })
    } catch (e) {
      if (jeton === generation.current) {
        setDetail(null)
        setErreur(e instanceof Error ? e.message : "Extraction illisible.")
      }
    }
  }, [])

  useEffect(() => {
    void chargerFile()
  }, [chargerFile])

  useEffect(() => {
    if (idActif !== null) void chargerDetail(idActif)
  }, [idActif, chargerDetail])

  async function apresMutation(texte: string) {
    setMessage(texte)
    setErreur(null)
    await chargerFile()
    if (idActif !== null) await chargerDetail(idActif)
  }

  async function resoudreLot() {
    setOccupe(true)
    try {
      const bilan = await jsonFetch<{ traitees: number; liees: number; ambigues: number }>(
        "/api/extractions-voile/resoudre-lot",
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ limite: 200 }) },
      )
      await apresMutation(
        `Résolution en lot : ${bilan.traitees} traitée(s), ${bilan.liees} liée(s) sur preuve exacte, ${bilan.ambigues} ambiguë(s) à décider.`,
      )
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "Résolution en lot refusée.")
    } finally {
      setOccupe(false)
    }
  }

  async function resoudreUne() {
    if (!detail) return
    setOccupe(true)
    try {
      await jsonFetch(`/api/extractions-voile/${detail.extraction.id_extraction}/resoudre`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      })
      await apresMutation("Résolution relancée (preuves exactes uniquement).")
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "Résolution refusée.")
    } finally {
      setOccupe(false)
    }
  }

  async function lier(corps: Record<string, unknown>) {
    if (!detail) return
    setOccupe(true)
    try {
      const resultat = await jsonFetch<{ modele: { nom_canonique: string }; alias_appris: unknown }>(
        `/api/extractions-voile/${detail.extraction.id_extraction}/lier`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(corps) },
      )
      await apresMutation(
        `Extraction liée à « ${resultat.modele.nom_canonique} »${resultat.alias_appris ? " — alias appris pour les suivantes." : "."}`,
      )
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "Liaison refusée.")
    } finally {
      setOccupe(false)
    }
  }

  async function corriger(champ: string, valeur: string) {
    if (!detail) return
    setOccupe(true)
    try {
      const resultat = await jsonFetch<{ propagation: { statut: string } }>(
        `/api/extractions-voile/${detail.extraction.id_extraction}/corriger`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            champ,
            valeur: valeur.trim() === "" ? null : valeur,
            motif: motif.trim() || null,
            revision_fiche: detail.fiche?.revision ?? null,
          }),
        },
      )
      await apresMutation(
        resultat.propagation.statut === "fiche"
          ? "Correction enregistrée et propagée à la fiche."
          : "Correction enregistrée sur l’extraction.",
      )
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "Correction refusée.")
      // En cas de conflit de révision, l’état affiché est périmé : on relit.
      if (e && typeof e === "object" && "status" in e && (e as { status?: number }).status === 409) {
        await chargerDetail(detail.extraction.id_extraction)
      }
    } finally {
      setOccupe(false)
    }
  }

  const extraction = detail?.extraction ?? null

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
          data-testid={erreur ? "erreur-extraction" : "message-extraction"}
        >
          {erreur ?? message}
        </div>
      )}
      <div className="grid min-h-0 flex-1 grid-cols-[20rem_minmax(0,1fr)_minmax(0,1.1fr)]">
        {/* file des extractions */}
        <aside className="flex min-h-0 flex-col border-r border-border">
          <header className="flex items-center justify-between border-b border-border p-3">
            <h2 className="text-sm font-semibold">Extractions de voiles</h2>
            <button type="button" onClick={() => void chargerFile()} className="text-muted-foreground hover:text-foreground" aria-label="Rafraîchir">
              <RefreshCw className="size-4" />
            </button>
          </header>
          <div className="space-y-2 border-b border-border p-3">
            <select
              value={filtreStatut}
              onChange={(e) => setFiltreStatut(e.target.value)}
              className="w-full rounded border border-border bg-input px-2 py-1 text-xs"
              data-testid="filtre-statut-liaison"
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
              placeholder="Code, bateau, type de voile…"
              className="w-full rounded border border-border bg-input px-2 py-1 text-xs"
              data-testid="recherche-extractions"
            />
            <button
              type="button"
              onClick={() => void resoudreLot()}
              disabled={occupe}
              className="flex w-full items-center justify-center gap-1 rounded border border-border px-2 py-1 text-xs font-semibold disabled:opacity-40"
              data-testid="bouton-resoudre-lot"
              title="Lie uniquement les correspondances exactes (clé, alias appris, bateau de la fiche) ; le reste attend une décision."
            >
              <Wand2 className="size-3.5" /> Résoudre le lot (preuves exactes)
            </button>
          </div>
          <ul className="min-h-0 flex-1 overflow-auto" data-testid="file-extractions">
            {file.length === 0 && (
              <li className="p-3 text-xs text-muted-foreground">Aucune extraction pour ce filtre.</li>
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
                  data-testid="extraction-item"
                  data-id={entree.id_extraction}
                >
                  <span className="block truncate font-mono font-semibold">
                    {entree.code_fiche ?? `#${entree.id_extraction}`}
                  </span>
                  <span className="block truncate text-muted-foreground">
                    {entree.type_voile ?? "Type non lu"} · {entree.modele_bateau_brut ?? "bateau non lu"}
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

        {/* détail : liaison bateau + valeurs corrigibles */}
        <section className="min-h-0 overflow-auto" data-testid="detail-extraction">
          {!extraction && (
            <p className="p-4 text-sm text-muted-foreground">
              Sélectionnez une extraction : le PDF source s’affiche à droite, les valeurs et le
              modèle de bateau se décident ici. Rien n’est lié ni corrigé sans votre décision.
            </p>
          )}
          {extraction && detail && (
            <div className="space-y-4 p-3">
              <header>
                <h2 className="font-mono text-sm font-semibold" data-testid="code-extraction">
                  {extraction.code_fiche ?? `#${extraction.id_extraction}`}{" "}
                  <a
                    href={`/comparaison?a=${extraction.id_extraction}`}
                    className="ml-2 rounded border border-border px-1.5 py-0.5 font-sans text-[11px] font-semibold hover:bg-accent"
                    data-testid="lien-comparer"
                  >
                    Comparer…
                  </a>{" "}
                  <a
                    href={`/confiance?id=${extraction.id_extraction}`}
                    className="ml-2 rounded border border-border px-1.5 py-0.5 font-sans text-[11px] font-semibold hover:bg-accent"
                    data-testid="lien-confiance"
                  >
                    Confiance…
                  </a>
                </h2>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {extraction.nom_fichier_source ?? "fichier source inconnu"}
                  {extraction.gabarit_code ? ` · gabarit ${extraction.gabarit_code}` : ""}
                  {extraction.score_qualite !== null
                    ? ` · score qualité ${(extraction.score_qualite * 100).toFixed(0)} %`
                    : ""}
                  {detail.fiche ? ` · fiche ${detail.fiche.code} (révision ${detail.fiche.revision})` : " · sans fiche liée"}
                </p>
                <p className="mt-1 flex gap-1 text-xs">
                  <span className="rounded bg-muted px-1.5 py-0.5">extraction : {extraction.statut_extraction}</span>
                  <span
                    className={cn(
                      "rounded px-1.5 py-0.5",
                      extraction.statut_plausibilite === "plausible"
                        ? "bg-emerald-500/20"
                        : "badge-attention",
                    )}
                    data-testid="statut-plausibilite"
                  >
                    plausibilité : {extraction.statut_plausibilite}
                  </span>
                </p>
              </header>

              <section className="rounded border border-border p-3" data-testid="panneau-liaison">
                <h3 className="flex items-center gap-1 text-xs font-semibold">
                  <Link2 className="size-3.5" /> Modèle de bateau
                </h3>
                <p className="mt-1 text-xs">
                  Libellé lu :{" "}
                  <span className="font-mono font-semibold" data-testid="libelle-brut">
                    {extraction.modele_bateau_brut ?? "—"}
                  </span>
                  {extraction.taille_bateau_brute ? ` (${extraction.taille_bateau_brute})` : ""}
                </p>
                <p className="mt-1 flex items-center gap-2 text-xs">
                  <BadgeLiaison statut={extraction.statut_liaison} />
                  {detail.modele_lie ? (
                    <span data-testid="modele-lie">
                      <span className="font-semibold">{detail.modele_lie.nom_canonique}</span>
                      {extraction.methode_liaison
                        ? ` — ${LIBELLES_METHODE[extraction.methode_liaison] ?? extraction.methode_liaison}`
                        : ""}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">aucun modèle canonique lié</span>
                  )}
                </p>
                {extraction.statut_liaison !== "lie_humain" && (
                  <div className="mt-2 space-y-2">
                    <button
                      type="button"
                      onClick={() => void resoudreUne()}
                      disabled={occupe}
                      className="rounded border border-border px-2 py-1 text-xs disabled:opacity-40"
                      data-testid="bouton-resoudre"
                    >
                      Réessayer la résolution automatique
                    </button>
                    {detail.candidats.length > 0 && (
                      <ul className="space-y-1" data-testid="candidats-modeles">
                        {detail.candidats.map((candidat) => (
                          <li key={candidat.id_modele} className="flex items-center justify-between gap-2 rounded bg-muted/50 px-2 py-1 text-xs">
                            <span>
                              <span className="font-semibold">{candidat.nom_canonique}</span>{" "}
                              <span className="text-muted-foreground">
                                ({candidat.type_correspondance}, score {candidat.score.toFixed(2)})
                              </span>
                            </span>
                            <button
                              type="button"
                              disabled={occupe}
                              onClick={() => void lier({ id_modele: candidat.id_modele })}
                              className="rounded bg-violet-600 px-2 py-0.5 font-semibold text-white disabled:opacity-40"
                              data-testid="bouton-lier-candidat"
                              data-id-modele={candidat.id_modele}
                            >
                              Lier
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                    <div className="flex items-center gap-2">
                      <input
                        value={nouveauModele}
                        onChange={(e) => setNouveauModele(e.target.value)}
                        placeholder="Nom du nouveau modèle canonique"
                        className="min-w-0 flex-1 rounded border border-border bg-input px-2 py-1 text-xs"
                        data-testid="nouveau-modele"
                      />
                      <button
                        type="button"
                        disabled={occupe || !nouveauModele.trim()}
                        onClick={() => void lier({ nouveau_modele: nouveauModele.trim() })}
                        className="rounded bg-violet-600 px-2 py-1 text-xs font-semibold text-white disabled:opacity-40"
                        data-testid="bouton-creer-lier"
                      >
                        Créer et lier
                      </button>
                    </div>
                    <p className="text-[11px] text-muted-foreground">
                      Votre décision apprend un alias : la prochaine extraction portant ce libellé
                      brut se liera toute seule. Un libellé déjà alias d’un autre modèle est refusé
                      (conflit explicite).
                    </p>
                  </div>
                )}
              </section>

              <section className="rounded border border-border p-3">
                <h3 className="text-xs font-semibold">Valeurs (PDF à droite pour vérifier)</h3>
                <div className="mt-2 flex items-center gap-2">
                  <input
                    value={motif}
                    onChange={(e) => setMotif(e.target.value)}
                    placeholder="Motif de correction (journalisé)"
                    className="w-full rounded border border-border bg-input px-2 py-1 text-xs"
                    data-testid="motif-correction"
                  />
                </div>
                <div className="mt-1 divide-y divide-border/60">
                  {CHAMPS_AFFICHES.map((champInfo) => {
                    const actuelle = valeurTexte(extraction[champInfo.nom])
                    const brouillon = brouillons[champInfo.nom] ?? actuelle
                    const modifie = brouillon !== actuelle
                    const provenance = provenancePour(extraction, champInfo.nom)
                    const suggestions = detail.suggestions_motifs.filter((s) => s.champ === champInfo.nom)
                    return (
                      <div key={champInfo.nom} className="grid grid-cols-[9rem_1fr_auto] items-center gap-2 py-1.5 text-xs">
                        <span className="text-muted-foreground">{champInfo.libelle}</span>
                        <span className="flex items-center gap-2">
                          <input
                            value={brouillon}
                            inputMode={champInfo.nombre ? "decimal" : undefined}
                            onChange={(e) => setBrouillons((b) => ({ ...b, [champInfo.nom]: e.target.value }))}
                            onFocus={() =>
                              setZone(
                                provenance?.zone
                                  ? {
                                      page: provenance.zone.page,
                                      x0: provenance.zone.x0,
                                      y0: provenance.zone.y0,
                                      x1: provenance.zone.x1,
                                      y1: provenance.zone.y1,
                                    }
                                  : null,
                              )
                            }
                            className="w-full rounded border border-transparent bg-input px-1.5 py-0.5 focus:border-primary"
                            data-testid={`champ-${champInfo.nom}`}
                          />
                          {modifie && (
                            <button
                              type="button"
                              disabled={occupe}
                              onClick={() => void corriger(champInfo.nom, brouillon)}
                              className="rounded bg-violet-600 px-1.5 py-0.5 font-semibold text-white disabled:opacity-40"
                              data-testid={`corriger-${champInfo.nom}`}
                            >
                              corriger
                            </button>
                          )}
                        </span>
                        <span className="flex items-center gap-1">
                          <span
                            className="max-w-36 truncate font-mono text-[10px] text-muted-foreground"
                            title={provenance ? `lu : ${provenance.valeur_brute ?? "—"} (page ${provenance.page ?? "?"})` : "provenance inconnue"}
                          >
                            {provenance?.valeur_brute ?? "—"}
                            {provenance?.confiance !== null && provenance?.confiance !== undefined
                              ? ` · ${(provenance.confiance * 100).toFixed(0)} %`
                              : ""}
                          </span>
                          {provenance?.zone && (
                            <button
                              type="button"
                              title="Voir la zone dans le PDF"
                              className="text-muted-foreground hover:text-foreground"
                              data-testid={`zone-${champInfo.nom}`}
                              onClick={() =>
                                setZone({
                                  page: provenance.zone!.page,
                                  x0: provenance.zone!.x0,
                                  y0: provenance.zone!.y0,
                                  x1: provenance.zone!.x1,
                                  y1: provenance.zone!.y1,
                                })
                              }
                            >
                              <Crosshair className="size-3.5" />
                            </button>
                          )}
                          {suggestions.map((suggestion) => (
                            <button
                              key={suggestion.valeur_corrigee}
                              type="button"
                              className="flex items-center gap-0.5 rounded bg-sky-500/20 px-1.5 py-0.5 text-[10px]"
                              title="Motif appris localement : déjà corrigé ainsi — à vous de confirmer."
                              data-testid="suggestion-motif"
                              onClick={() => setBrouillons((b) => ({ ...b, [champInfo.nom]: suggestion.valeur_corrigee }))}
                            >
                              <Sparkles className="size-3" /> déjà corrigé en {suggestion.valeur_corrigee} ({suggestion.occurrences}×)
                            </button>
                          ))}
                        </span>
                      </div>
                    )
                  })}
                </div>
                {extraction.anomalies.length > 0 && (
                  <ul className="mt-2 space-y-1" data-testid="anomalies">
                    {extraction.anomalies.map((anomalie, i) => (
                      <li key={`${anomalie.code}-${i}`} className="rounded bg-amber-500/10 px-2 py-1 text-[11px]">
                        <span className="font-mono">{anomalie.code}</span> — {anomalie.message}
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section className="rounded border border-border p-3" data-testid="historique-corrections">
                <h3 className="text-xs font-semibold">Corrections approbateur</h3>
                {detail.corrections.length === 0 ? (
                  <p className="mt-1 text-xs text-muted-foreground">Aucune correction pour l’instant.</p>
                ) : (
                  <ul className="mt-1 space-y-1 text-xs">
                    {detail.corrections.map((correction) => (
                      <li key={correction.id_correction} className="border-b border-border/40 py-1">
                        <span className="font-mono">{correction.champ}</span> :{" "}
                        {valeurTexte(correction.valeur_avant)} → {valeurTexte(correction.valeur_apres)}
                        <span className="text-muted-foreground">
                          {" "}
                          par {correction.corrige_par ?? "?"}
                          {correction.propagation === "fiche" ? " · propagée à la fiche" : ""}
                          {correction.motif ? ` — ${correction.motif}` : ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>
          )}
        </section>

        {/* visionneuse PDF */}
        <aside className="min-h-0 border-l border-border" data-testid="panneau-pdf">
          <VisionneusePiece
            // Jamais le PDF d'une autre extraction : la pièce est étiquetée par
            // l'extraction active, comme sur l'écran de validation.
            piece={piecePdf && extraction && piecePdf.idExtraction === extraction.id_extraction ? piecePdf.piece : null}
            zone={zone}
          />
        </aside>
      </div>
    </div>
  )
}

export function ExtractionsApp() {
  const { session, role, chargement } = useSession()
  if (chargement) {
    return <div className="p-6 text-sm text-muted-foreground">Vérification de votre rôle…</div>
  }
  if (session.mode !== "nominatif" || !estApprobateur(role)) {
    return (
      <AccesRefuse message="La liaison des modèles de bateau et la correction des extractions sont réservées à un approbateur nominatif. Les autres comptes consultent les fiches et utilisent l’écran de recherche." />
    )
  }
  return <ExtractionsAppInterne />
}

