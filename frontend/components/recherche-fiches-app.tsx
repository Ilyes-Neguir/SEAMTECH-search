"use client"

// Écran RECHERCHE (lot E + J) : recherche hybride des fiches techniques —
// « comme Google » : une barre, des suggestions issues des valeurs RÉELLES
// du corpus, des facettes à compteurs, des résultats fusionnés (RRF),
// facette DIMENSION (cotes), tri, pagination, URL partageable.
// L'écran Phase 0 de recherche fichiers vit désormais sur /fichiers.

import { useCallback, useEffect, useRef, useState } from "react"
import { Search, X, Link as LinkIcon, Check } from "lucide-react"
import { authedFetch } from "@/lib/authed-fetch"
import { cn } from "@/lib/utils"
import { AssistantPanneau } from "@/components/assistant-panneau"

interface FacetteEntree {
  valeur: string
  effectif: number
}

interface IntervalleCote {
  min: number
  max: number
  effectif: number
  label: string
}

interface FacetteCote {
  unite: string
  min: number | null
  max: number | null
  effectif: number
  intervalles: IntervalleCote[]
}

interface ResultatFiche {
  code: string | null
  titre: string | null
  type_voile: string | null
  client: string | null
  bateau: string | null
  gamme: string | null
  statut: string | null
  annee: number | null
  extrait: string
  score: number
  sources: string[]
  slu_m?: number | null
  sle_m?: number | null
  sf_m?: number | null
  shw_m?: number | null
  spa_m2?: number | null
  tetiere_cm?: number | null
  poids_kg?: number | null
}

interface ReponseRecherche {
  requete: string
  nb_resultats: number
  resultats: ResultatFiche[]
  facettes: Record<string, FacetteEntree[]>
  facettes_cotes?: Record<string, FacetteCote>
  cote_active?: string
  cotes_unites?: Record<string, string>
  tri?: string
  page?: number
  limit?: number
  has_more?: boolean
  sources_actives: string[]
  sans_resultat: boolean
  duree_ms: number
}

interface Suggestion {
  nature: string
  valeur: string
}

const GROUPES_FACETTES: Array<{ cle: string; libelle: string }> = [
  { cle: "type_voile", libelle: "Type de voile" },
  { cle: "client", libelle: "Client" },
  { cle: "bateau", libelle: "Bateau" },
  { cle: "matiere", libelle: "Matière" },
  { cle: "gamme", libelle: "Gamme" },
  { cle: "annee", libelle: "Année" },
]

const COTES_LISTE: Array<{ cle: string; libelle: string; unite: string }> = [
  { cle: "slu_m", libelle: "SLU", unite: "m" },
  { cle: "sle_m", libelle: "SLE", unite: "m" },
  { cle: "sf_m", libelle: "SF", unite: "m" },
  { cle: "shw_m", libelle: "SHW", unite: "m" },
  { cle: "spa_m2", libelle: "SPA", unite: "m²" },
  { cle: "tetiere_cm", libelle: "Têtière", unite: "cm" },
  { cle: "poids_kg", libelle: "Poids", unite: "kg" },
]

const TRIS: Array<{ valeur: string; libelle: string }> = [
  { valeur: "pertinence", libelle: "Pertinence" },
  { valeur: "date_desc", libelle: "Date ↓" },
  { valeur: "date_asc", libelle: "Date ↑" },
  { valeur: "code_asc", libelle: "Code A→Z" },
  { valeur: "code_desc", libelle: "Code Z→A" },
  { valeur: "slu_m_asc", libelle: "SLU ↑" },
  { valeur: "slu_m_desc", libelle: "SLU ↓" },
  { valeur: "sle_m_asc", libelle: "SLE ↑" },
  { valeur: "sle_m_desc", libelle: "SLE ↓" },
  { valeur: "sf_m_asc", libelle: "SF ↑" },
  { valeur: "sf_m_desc", libelle: "SF ↓" },
  { valeur: "shw_m_asc", libelle: "SHW ↑" },
  { valeur: "shw_m_desc", libelle: "SHW ↓" },
  { valeur: "spa_m2_asc", libelle: "SPA ↑" },
  { valeur: "spa_m2_desc", libelle: "SPA ↓" },
  { valeur: "tetiere_cm_asc", libelle: "Têtière ↑" },
  { valeur: "tetiere_cm_desc", libelle: "Têtière ↓" },
  { valeur: "poids_kg_asc", libelle: "Poids ↑" },
  { valeur: "poids_kg_desc", libelle: "Poids ↓" },
]

const FILTRES_CLES = [
  "type_voile",
  "client",
  "bateau",
  "matiere",
  "gamme",
  "annee",
  "annee_min",
  "annee_max",
  "inclure_a_valider",
]

const LIBELLE_SOURCE: Record<string, string> = {
  lexical: "Lexical",
  trigrammes: "Faute tolérée",
  texte_pdf: "Texte PDF",
  vecteurs: "Vecteurs",
  parcours: "Parcours",
}

const NATURE_SUGGESTION: Record<string, string> = {
  type_voile: "type",
  bateau: "bateau",
  client: "client",
  matiere: "matière",
  gamme: "gamme",
  fiche: "code",
}

const LIMITE_DEFAUT = 20

async function jsonFetch<T>(url: string): Promise<T> {
  const res = await authedFetch(url)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(String(body?.detail ?? `HTTP ${res.status}`))
  return body as T
}

type DimensionFiltre = { cote: string; min: string; max: string } | null

function parseUrl(): {
  q: string
  filtres: Record<string, string>
  dimCote: string
  dimMin: string
  dimMax: string
  tri: string
  page: number
} {
  if (typeof window === "undefined") {
    return { q: "", filtres: {}, dimCote: "slu_m", dimMin: "", dimMax: "", tri: "pertinence", page: 1 }
  }
  const params = new URLSearchParams(window.location.search)
  const q = params.get("q") ?? ""
  const filtres: Record<string, string> = {}
  for (const cle of FILTRES_CLES) {
    const v = params.get(cle)
    if (v) filtres[cle] = v
  }
  const cote = params.get("cote") ?? "slu_m"
  const min = params.get("min") ?? params.get("cote_min") ?? ""
  const max = params.get("max") ?? params.get("cote_max") ?? ""
  const tri = params.get("tri") ?? "pertinence"
  const pageRaw = params.get("page")
  let page = 1
  if (pageRaw) {
    const n = parseInt(pageRaw, 10)
    if (!Number.isNaN(n) && n >= 1) page = n
  }
  return { q, filtres, dimCote: cote, dimMin: min, dimMax: max, tri, page }
}

function buildBrowserUrl(
  q: string,
  filtres: Record<string, string>,
  dimCote: string,
  dimMin: string,
  dimMax: string,
  tri: string,
  page: number,
): string {
  const params = new URLSearchParams()
  if (q.trim()) params.set("q", q.trim())
  for (const [k, v] of Object.entries(filtres)) {
    if (v) params.set(k, v)
  }
  if (dimCote && dimCote !== "slu_m") params.set("cote", dimCote)
  else if (dimCote) {
    // On garde cote même si slu_m pour partageable explicite si filtre actif ou exploration différente ?
    // Pour URL minimale, on ne met cote=slu_m que si un filtre min/max est actif.
    if (dimMin || dimMax) params.set("cote", dimCote)
  }
  // Si cote=slu_m par défaut sans filtre, on ne l'écrit pas sauf si besoin de partager l'exploration.
  // On choisit : écrire cote dès qu'il diffère de slu_m OU qu'un min/max est présent.
  // Pour permettre partage de l'exploration slu_m explicite, on écrit aussi si l'URL d'origine l'avait ?
  // Simplification : si dimCote !== "slu_m" on écrit déjà ; sinon si min/max présent on écrit.
  if (dimMin) params.set("min", dimMin)
  if (dimMax) params.set("max", dimMax)
  if (tri && tri !== "pertinence") params.set("tri", tri)
  if (page && page > 1) params.set("page", String(page))
  const qs = params.toString()
  return qs ? `?${qs}` : window.location.pathname
}

function buildApiParams(
  q: string,
  filtres: Record<string, string>,
  dim: DimensionFiltre,
  tri: string,
  page: number,
  limit: number,
): URLSearchParams {
  const params = new URLSearchParams()
  if (q.trim()) params.set("q", q.trim())
  for (const [k, v] of Object.entries(filtres)) {
    if (v) params.set(k, v)
  }
  if (dim) {
    if (dim.cote) params.set("cote", dim.cote)
    if (dim.min) params.set("min", dim.min)
    if (dim.max) params.set("max", dim.max)
  }
  if (tri) params.set("tri", tri)
  if (page) params.set("page", String(page))
  if (limit) params.set("limit", String(limit))
  return params
}

export function RechercheFichesApp() {
  const [saisie, setSaisie] = useState("")
  const [requete, setRequete] = useState("")
  const [filtres, setFiltres] = useState<Record<string, string>>({})
  const [dimCote, setDimCote] = useState("slu_m")
  const [dimMin, setDimMin] = useState("")
  const [dimMax, setDimMax] = useState("")
  const [tri, setTri] = useState("pertinence")
  const [page, setPage] = useState(1)
  const [reponse, setReponse] = useState<ReponseRecherche | null>(null)
  const [chargement, setChargement] = useState(false)
  const [erreur, setErreur] = useState<string | null>(null)
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [suggestionsVisibles, setSuggestionsVisibles] = useState(false)
  const [copieOk, setCopieOk] = useState(false)
  const rechercheRef = useRef(0)
  const initialLoadRef = useRef(true)

  const lancer = useCallback(
    async (
      q: string,
      prochainsFiltres: Record<string, string>,
      prochainDimCote: string,
      prochainDimMin: string,
      prochainDimMax: string,
      prochainTri: string,
      prochainePage: number,
    ) => {
      const id = ++rechercheRef.current
      setChargement(true)
      setErreur(null)
      try {
        const dimFiltre: DimensionFiltre =
          prochainDimCote && (prochainDimMin || prochainDimMax)
            ? { cote: prochainDimCote, min: prochainDimMin, max: prochainDimMax }
            : prochainDimCote && prochainDimCote !== "slu_m" && (prochainDimMin === "" && prochainDimMax === "" && new URLSearchParams(window.location.search).has("cote"))
              ? null // exploration seule, pas de filtre si pas de min/max et pas déjà en URL comme filtre ?
              : prochainDimMin || prochainDimMax
                ? { cote: prochainDimCote || "slu_m", min: prochainDimMin, max: prochainDimMax }
                : prochainDimCote !== "slu_m"
                  ? null // exploration seule, on ne filtre pas, mais on garde cote pour cote_active
                  : null

        // Pour l'API : si on a min/max, on envoie cote+min+max ; si on a seulement cote d'exploration
        // (sans min/max) on envoie quand même cote pour que backend retourne cote_active correspondante,
        // sans filtrer. Donc dimApi = cote présent même sans min/max si exploration != défaut.
        let dimApi: DimensionFiltre
        if (prochainDimMin || prochainDimMax) {
          dimApi = { cote: prochainDimCote || "slu_m", min: prochainDimMin, max: prochainDimMax }
        } else if (prochainDimCote && prochainDimCote !== "slu_m") {
          dimApi = { cote: prochainDimCote, min: "", max: "" }
        } else {
          dimApi = null
        }

        // Si dimFiltre est null mais dimApi a cote seul, on envoie cote seul pour exploration
        const params = buildApiParams(q, prochainsFiltres, dimApi, prochainTri, prochainePage, LIMITE_DEFAUT)
        const corps = await jsonFetch<ReponseRecherche>(`/api/recherche?${params.toString()}`)
        if (id !== rechercheRef.current) return
        setReponse(corps)
        setRequete(q.trim())
        setPage(corps.page ?? prochainePage)
        if (corps.tri) setTri(corps.tri)
        // Met à jour l'URL partageable
        const browserUrl = buildBrowserUrl(q, prochainsFiltres, prochainDimCote, prochainDimMin, prochainDimMax, prochainTri, prochainePage)
        window.history.replaceState(null, "", browserUrl)
      } catch (e) {
        if (id !== rechercheRef.current) return
        setErreur(e instanceof Error ? e.message : "Recherche impossible.")
      } finally {
        if (id === rechercheRef.current) setChargement(false)
      }
    },
    [],
  )

  // Chargement initial depuis URL
  useEffect(() => {
    const { q, filtres: f, dimCote: dc, dimMin: dmin, dimMax: dmax, tri: t, page: p } = parseUrl()
    setSaisie(q)
    setRequete(q)
    setFiltres(f)
    setDimCote(dc)
    setDimMin(dmin)
    setDimMax(dmax)
    setTri(t)
    setPage(p)
    void lancer(q, f, dc, dmin, dmax, t, p)
    initialLoadRef.current = false
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Suggestions au fil de la frappe (250 ms)
  useEffect(() => {
    const prefixe = saisie.trim()
    if (prefixe.length < 1) {
      setSuggestions([])
      return
    }
    const minuterie = setTimeout(async () => {
      try {
        const corps = await jsonFetch<{ suggestions: Suggestion[] }>(
          `/api/recherche/suggestions?prefix=${encodeURIComponent(prefixe)}`,
        )
        setSuggestions(corps.suggestions)
      } catch {
        setSuggestions([])
      }
    }, 250)
    return () => clearTimeout(minuterie)
  }, [saisie])

  const choisirSuggestion = useCallback(
    (valeur: string) => {
      setSaisie(valeur)
      setSuggestionsVisibles(false)
      void lancer(valeur, filtres, dimCote, dimMin, dimMax, tri, 1)
      setPage(1)
    },
    [filtres, dimCote, dimMin, dimMax, tri, lancer],
  )

  const basculerFiltre = useCallback(
    (cle: string, valeur: string) => {
      setFiltres((precedents) => {
        const prochains = { ...precedents }
        if (prochains[cle] === valeur) delete prochains[cle]
        else prochains[cle] = valeur
        void lancer(requete, prochains, dimCote, dimMin, dimMax, tri, 1)
        setPage(1)
        return prochains
      })
    },
    [requete, dimCote, dimMin, dimMax, tri, lancer],
  )

  const retirerFiltre = useCallback(
    (cle: string) => {
      setFiltres((precedents) => {
        const prochains = { ...precedents }
        delete prochains[cle]
        void lancer(requete, prochains, dimCote, dimMin, dimMax, tri, 1)
        setPage(1)
        return prochains
      })
    },
    [requete, dimCote, dimMin, dimMax, tri, lancer],
  )

  const appliquerDimension = useCallback(
    (cote: string, min: string, max: string) => {
      setDimCote(cote)
      setDimMin(min)
      setDimMax(max)
      void lancer(requete, filtres, cote, min, max, tri, 1)
      setPage(1)
    },
    [requete, filtres, tri, lancer],
  )

  const retirerDimension = useCallback(() => {
    setDimMin("")
    setDimMax("")
    // On garde cote d'exploration mais sans filtre
    void lancer(requete, filtres, dimCote, "", "", tri, 1)
    setPage(1)
  }, [requete, filtres, dimCote, tri, lancer])

  const changerTri = useCallback(
    (nouveauTri: string) => {
      setTri(nouveauTri)
      void lancer(requete, filtres, dimCote, dimMin, dimMax, nouveauTri, 1)
      setPage(1)
    },
    [requete, filtres, dimCote, dimMin, dimMax, lancer],
  )

  const changerPage = useCallback(
    (nouvellePage: number) => {
      setPage(nouvellePage)
      void lancer(requete, filtres, dimCote, dimMin, dimMax, tri, nouvellePage)
      window.scrollTo({ top: 0, behavior: "smooth" })
    },
    [requete, filtres, dimCote, dimMin, dimMax, tri, lancer],
  )

  const changerCoteExploration = useCallback(
    (nouvelleCote: string) => {
      setDimCote(nouvelleCote)
      // Si un filtre dimension est actif, on garde min/max mais on change cote du filtre
      // Sinon on change juste l'exploration (envoi cote seul à l'API pour mettre à jour cote_active)
      const hasFiltre = dimMin || dimMax
      if (hasFiltre) {
        void lancer(requete, filtres, nouvelleCote, dimMin, dimMax, tri, 1)
        setPage(1)
      } else {
        // Exploration seule : on envoie cote sans min/max pour que backend retourne bons intervalles
        void lancer(requete, filtres, nouvelleCote, "", "", tri, 1)
        setPage(1)
      }
    },
    [requete, filtres, dimMin, dimMax, tri, lancer],
  )

  const copierLien = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(window.location.href)
      setCopieOk(true)
      setTimeout(() => setCopieOk(false), 2000)
    } catch {
      // fallback
      const ta = document.createElement("textarea")
      ta.value = window.location.href
      document.body.appendChild(ta)
      ta.select()
      document.execCommand("copy")
      document.body.removeChild(ta)
      setCopieOk(true)
      setTimeout(() => setCopieOk(false), 2000)
    }
  }, [])

  const facettesActives = reponse
    ? GROUPES_FACETTES.filter(({ cle }) => (reponse.facettes[cle] ?? []).length > 0)
    : []

  const facetteCoteCourante: FacetteCote | undefined = reponse?.facettes_cotes?.[dimCote] ?? reponse?.facettes_cotes?.[reponse?.cote_active ?? "slu_m"]
  const totalPages = reponse ? Math.max(1, Math.ceil(reponse.nb_resultats / LIMITE_DEFAUT)) : 1

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6" data-testid="recherche-fiches">
      {/* Barre de recherche + tri + copier lien */}
      <form
        className="relative"
        onSubmit={(e) => {
          e.preventDefault()
          setSuggestionsVisibles(false)
          void lancer(saisie, filtres, dimCote, dimMin, dimMax, tri, 1)
          setPage(1)
        }}
      >
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex flex-1 items-center gap-2 rounded-lg border border-border bg-background px-3 py-2.5 focus-within:border-foreground/40">
            <Search className="size-4 shrink-0 text-muted-foreground" />
            <input
              value={saisie}
              onChange={(e) => {
                setSaisie(e.target.value)
                setSuggestionsVisibles(true)
              }}
              onFocus={() => setSuggestionsVisibles(true)}
              onBlur={() => setTimeout(() => setSuggestionsVisibles(false), 150)}
              placeholder="Rechercher une fiche — code, voile, client, matière…"
              className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              data-testid="recherche-saisie"
              autoFocus
            />
            <button
              type="submit"
              className="rounded bg-accent px-3 py-1 text-xs font-medium text-accent-foreground hover:opacity-90"
            >
              Rechercher
            </button>
          </div>
          <select
            value={tri}
            onChange={(e) => changerTri(e.target.value)}
            className="rounded border border-border bg-background px-2 py-2 text-xs"
            data-testid="recherche-tri"
          >
            {TRIS.map((t) => (
              <option key={t.valeur} value={t.valeur}>
                {t.libelle}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={copierLien}
            className="flex items-center gap-1 rounded border border-border bg-background px-3 py-2 text-xs hover:bg-accent/50"
            data-testid="copier-lien"
          >
            {copieOk ? <Check className="size-3" /> : <LinkIcon className="size-3" />}
            {copieOk ? "Lien copié" : "Copier le lien"}
          </button>
        </div>
        {suggestionsVisibles && suggestions.length > 0 && (
          <ul
            className="absolute z-30 mt-1 w-full overflow-hidden rounded-lg border border-border bg-background shadow-lg"
            data-testid="recherche-suggestions"
          >
            {suggestions.map((s) => (
              <li key={`${s.nature}:${s.valeur}`}>
                <button
                  type="button"
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-accent/50"
                  onMouseDown={(e) => {
                    e.preventDefault()
                    choisirSuggestion(s.valeur)
                  }}
                >
                  <Search className="size-3.5 shrink-0 text-muted-foreground" />
                  <span>{s.valeur}</span>
                  <span className="ml-auto text-[10px] uppercase tracking-wide text-muted-foreground">
                    {NATURE_SUGGESTION[s.nature] ?? s.nature}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </form>

      {/* Assistant sourcé */}
      <div className="mt-4">
        <AssistantPanneau />
      </div>

      {/* Filtres actifs */}
      {(Object.keys(filtres).length > 0 || dimMin || dimMax) && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {Object.entries(filtres).map(([cle, valeur]) => (
            <button
              key={cle}
              type="button"
              onClick={() => retirerFiltre(cle)}
              className="flex items-center gap-1.5 rounded-full border border-border bg-accent/30 px-3 py-1 text-xs"
            >
              <span className="text-muted-foreground">{GROUPE_LIBELLE(cle)} :</span> {valeur}
              <X className="size-3" />
            </button>
          ))}
          {(dimMin || dimMax) && (
            <button
              type="button"
              onClick={retirerDimension}
              className="flex items-center gap-1.5 rounded-full border border-border bg-accent/50 px-3 py-1 text-xs"
              data-testid="filtre-dimension-actif"
            >
              <span className="text-muted-foreground">
                {COTES_LISTE.find((c) => c.cle === dimCote)?.libelle ?? dimCote} :
              </span>{" "}
              {dimMin || "…"} – {dimMax || "…"} {COTES_LISTE.find((c) => c.cle === dimCote)?.unite ?? ""}
              <X className="size-3" />
            </button>
          )}
        </div>
      )}

      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-[240px_1fr]">
        {/* Facettes */}
        <aside className="space-y-6">
          {facettesActives.map(({ cle, libelle }) => (
            <div key={cle}>
              <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                {libelle}
              </div>
              <ul className="space-y-0.5">
                {(reponse?.facettes[cle] ?? []).map((entree) => (
                  <li key={entree.valeur}>
                    <button
                      type="button"
                      onClick={() => basculerFiltre(cle, entree.valeur)}
                      className={cn(
                        "flex w-full items-center justify-between rounded px-2 py-1 text-left text-xs hover:bg-accent/50",
                        filtres[cle] === entree.valeur && "bg-accent text-accent-foreground",
                      )}
                    >
                      <span className="truncate">{entree.valeur}</span>
                      <span className="ml-2 shrink-0 tabular-nums text-muted-foreground">{entree.effectif}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}

          {/* Facette DIMENSION */}
          <div data-testid="facette-dimension">
            <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Dimension
            </div>
            <select
              value={dimCote}
              onChange={(e) => changerCoteExploration(e.target.value)}
              className="mb-2 w-full rounded border border-border bg-background px-2 py-1.5 text-xs"
              data-testid="dimension-cote"
            >
              {COTES_LISTE.map((c) => (
                <option key={c.cle} value={c.cle}>
                  {c.libelle} ({c.unite})
                </option>
              ))}
            </select>

            {facetteCoteCourante && (
              <div className="mb-2 text-[10px] text-muted-foreground">
                {facetteCoteCourante.effectif} fiches · {facetteCoteCourante.min ?? "–"} – {facetteCoteCourante.max ?? "–"}{" "}
                {facetteCoteCourante.unite}
              </div>
            )}

            <div className="mb-2 flex gap-1">
              <input
                type="number"
                step="any"
                placeholder="min"
                value={dimMin}
                onChange={(e) => setDimMin(e.target.value)}
                className="w-1/2 rounded border border-border bg-background px-2 py-1 text-xs"
                data-testid="dimension-min"
              />
              <input
                type="number"
                step="any"
                placeholder="max"
                value={dimMax}
                onChange={(e) => setDimMax(e.target.value)}
                className="w-1/2 rounded border border-border bg-background px-2 py-1 text-xs"
                data-testid="dimension-max"
              />
            </div>
            <div className="mb-3 flex gap-1">
              <button
                type="button"
                onClick={() => appliquerDimension(dimCote, dimMin, dimMax)}
                className="flex-1 rounded bg-accent px-2 py-1 text-xs font-medium text-accent-foreground hover:opacity-90"
                data-testid="dimension-appliquer"
              >
                Filtrer
              </button>
              {(dimMin || dimMax) && (
                <button
                  type="button"
                  onClick={retirerDimension}
                  className="rounded border border-border px-2 py-1 text-xs hover:bg-accent/30"
                >
                  Effacer
                </button>
              )}
            </div>

            {/* Intervalles calculés depuis données réelles */}
            {facetteCoteCourante?.intervalles && facetteCoteCourante.intervalles.length > 0 && (
              <ul className="space-y-0.5" data-testid="dimension-intervalles">
                {facetteCoteCourante.intervalles.map((iv, idx) => (
                  <li key={`${iv.min}-${iv.max}-${idx}`}>
                    <button
                      type="button"
                      onClick={() => appliquerDimension(dimCote, String(iv.min), String(iv.max))}
                      className={cn(
                        "flex w-full items-center justify-between rounded px-2 py-1 text-left text-xs hover:bg-accent/50",
                        dimMin === String(iv.min) && dimMax === String(iv.max) && "bg-accent text-accent-foreground",
                      )}
                    >
                      <span className="truncate">{iv.label}</span>
                      <span className="ml-2 shrink-0 tabular-nums text-muted-foreground">{iv.effectif}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </aside>

        {/* Résultats */}
        <section>
          {chargement && <p className="text-sm text-muted-foreground">Recherche…</p>}
          {erreur && !chargement && (
            <p className="rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{erreur}</p>
          )}
          {!chargement && !erreur && reponse && (
            <>
              <p className="mb-4 text-xs text-muted-foreground">
                {reponse.nb_resultats} fiche{reponse.nb_resultats === 1 ? "" : "s"}
                {requete && (
                  <>
                    {" "}
                    pour « <span className="text-foreground/80">{requete}</span> »
                  </>
                )}{" "}
                · {reponse.duree_ms} ms · tri : {TRIS.find((t) => t.valeur === (reponse.tri ?? tri))?.libelle ?? tri} · sources :{" "}
                {reponse.sources_actives.map((s) => LIBELLE_SOURCE[s] ?? s).join(", ") || "—"} · page {reponse.page ?? page}/
                {totalPages}
              </p>
              {reponse.sans_resultat && (
                <div
                  className="rounded border border-border bg-background/60 px-4 py-6 text-center"
                  data-testid="recherche-sans-resultat"
                >
                  <p className="text-sm">Aucune fiche ne correspond.</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Cette recherche est comptabilisée pour améliorer le lexique.
                  </p>
                </div>
              )}
              <ul className="space-y-2">
                {reponse.resultats.map((fiche, position) => (
                  <li key={`${fiche.code ?? position}`}>
                    <article className="rounded-lg border border-border bg-background/60 px-4 py-3">
                      <div className="flex items-baseline gap-2">
                        <span className="font-mono text-xs text-muted-foreground">{fiche.code ?? "—"}</span>
                        <h2 className="text-sm font-medium">{fiche.titre ?? "Sans titre"}</h2>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {[fiche.type_voile, fiche.client, fiche.bateau, fiche.annee, fiche.gamme]
                          .filter((v): v is string | number => v !== null && v !== "")
                          .join(" · ")}
                      </p>
                      {/* Cotes */}
                      {(fiche.slu_m != null ||
                        fiche.sle_m != null ||
                        fiche.sf_m != null ||
                        fiche.shw_m != null ||
                        fiche.spa_m2 != null ||
                        fiche.tetiere_cm != null ||
                        fiche.poids_kg != null) && (
                        <p className="mt-1 text-[11px] text-muted-foreground">
                          {[
                            fiche.slu_m != null ? `SLU ${fiche.slu_m}m` : null,
                            fiche.sle_m != null ? `SLE ${fiche.sle_m}m` : null,
                            fiche.sf_m != null ? `SF ${fiche.sf_m}m` : null,
                            fiche.shw_m != null ? `SHW ${fiche.shw_m}m` : null,
                            fiche.spa_m2 != null ? `SPA ${fiche.spa_m2}m²` : null,
                            fiche.tetiere_cm != null ? `Têtière ${fiche.tetiere_cm}cm` : null,
                            fiche.poids_kg != null ? `${fiche.poids_kg}kg` : null,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </p>
                      )}
                      <div className="mt-2 flex items-center gap-1.5">
                        {fiche.sources.map((s) => (
                          <span
                            key={s}
                            className="rounded-full border border-border px-2 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground"
                          >
                            {LIBELLE_SOURCE[s] ?? s}
                          </span>
                        ))}
                      </div>
                    </article>
                  </li>
                ))}
              </ul>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="mt-6 flex items-center justify-center gap-1" data-testid="pagination">
                  <button
                    type="button"
                    onClick={() => changerPage(Math.max(1, page - 1))}
                    disabled={page <= 1}
                    className="rounded border border-border px-2 py-1 text-xs disabled:opacity-40"
                  >
                    Précédent
                  </button>
                  {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
                    let p: number
                    if (totalPages <= 7) p = i + 1
                    else if (page <= 4) p = i + 1
                    else if (page >= totalPages - 3) p = totalPages - 6 + i
                    else p = page - 3 + i
                    return (
                      <button
                        key={p}
                        type="button"
                        onClick={() => changerPage(p)}
                        className={cn(
                          "rounded px-2 py-1 text-xs",
                          p === page ? "bg-accent text-accent-foreground" : "border border-border hover:bg-accent/30",
                        )}
                      >
                        {p}
                      </button>
                    )
                  })}
                  <button
                    type="button"
                    onClick={() => changerPage(Math.min(totalPages, page + 1))}
                    disabled={page >= totalPages}
                    className="rounded border border-border px-2 py-1 text-xs disabled:opacity-40"
                  >
                    Suivant
                  </button>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  )
}

function GROUPE_LIBELLE(cle: string): string {
  return GROUPES_FACETTES.find((g) => g.cle === cle)?.libelle ?? cle
}
