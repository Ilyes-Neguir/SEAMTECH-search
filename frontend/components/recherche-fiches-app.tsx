"use client"

// Écran RECHERCHE (lot E) : recherche hybride des fiches techniques —
// « comme Google » : une barre, des suggestions issues des valeurs RÉELLES
// du corpus, des facettes à compteurs, des résultats fusionnés (RRF).
// L'écran Phase 0 de recherche fichiers vit désormais sur /fichiers.

import { useCallback, useEffect, useRef, useState } from "react"
import { Search, X } from "lucide-react"
import { authedFetch } from "@/lib/authed-fetch"
import { cn } from "@/lib/utils"
import { AssistantPanneau } from "@/components/assistant-panneau"

interface FacetteEntree {
  valeur: string
  effectif: number
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
}

interface ReponseRecherche {
  requete: string
  nb_resultats: number
  resultats: ResultatFiche[]
  facettes: Record<string, FacetteEntree[]>
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

async function jsonFetch<T>(url: string): Promise<T> {
  const res = await authedFetch(url)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(String(body?.detail ?? `HTTP ${res.status}`))
  return body as T
}

export function RechercheFichesApp() {
  const [saisie, setSaisie] = useState("")
  const [requete, setRequete] = useState("")
  const [filtres, setFiltres] = useState<Record<string, string>>({})
  const [reponse, setReponse] = useState<ReponseRecherche | null>(null)
  const [chargement, setChargement] = useState(false)
  const [erreur, setErreur] = useState<string | null>(null)
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [suggestionsVisibles, setSuggestionsVisibles] = useState(false)
  const rechercheRef = useRef(0)

  const lancer = useCallback(async (q: string, prochainsFiltres: Record<string, string>) => {
    const id = ++rechercheRef.current
    setChargement(true)
    setErreur(null)
    try {
      const params = new URLSearchParams()
      if (q.trim()) params.set("q", q.trim())
      for (const [cle, valeur] of Object.entries(prochainsFiltres)) {
        if (valeur) params.set(cle, valeur)
      }
      const corps = await jsonFetch<ReponseRecherche>(`/api/recherche?${params.toString()}`)
      if (id !== rechercheRef.current) return
      setReponse(corps)
      setRequete(q.trim())
    } catch (e) {
      if (id !== rechercheRef.current) return
      setErreur(e instanceof Error ? e.message : "Recherche impossible.")
    } finally {
      if (id === rechercheRef.current) setChargement(false)
    }
  }, [])

  useEffect(() => {
    void lancer("", {})
  }, [lancer])

  // Suggestions au fil de la frappe (250 ms de repos, comme un moteur grand
  // public) — valeurs réellement présentes uniquement, jamais de lexique
  // inventé (RG : le vrai précède l'approximatif).
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
      void lancer(valeur, filtres)
    },
    [filtres, lancer],
  )

  const basculerFiltre = useCallback(
    (cle: string, valeur: string) => {
      setFiltres((precedents) => {
        const prochains = { ...precedents }
        if (prochains[cle] === valeur) delete prochains[cle]
        else prochains[cle] = valeur
        void lancer(requete, prochains)
        return prochains
      })
    },
    [requete, lancer],
  )

  const retirerFiltre = useCallback(
    (cle: string) => {
      setFiltres((precedents) => {
        const prochains = { ...precedents }
        delete prochains[cle]
        void lancer(requete, prochains)
        return prochains
      })
    },
    [requete, lancer],
  )

  const facettesActives = reponse
    ? GROUPES_FACETTES.filter(({ cle }) => (reponse.facettes[cle] ?? []).length > 0)
    : []

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6" data-testid="recherche-fiches">
      {/* Barre de recherche */}
      <form
        className="relative"
        onSubmit={(e) => {
          e.preventDefault()
          setSuggestionsVisibles(false)
          void lancer(saisie, filtres)
        }}
      >
        <div className="flex items-center gap-2 rounded-lg border border-border bg-background px-3 py-2.5 focus-within:border-foreground/40">
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

      {/* Assistant sourcé (lot I) : question → réponse extraite de la base,
          citations cliquables — jamais de valeur sans source. */}
      <div className="mt-4">
        <AssistantPanneau />
      </div>

      {/* Filtres actifs */}
      {Object.keys(filtres).length > 0 && (
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
        </div>
      )}

      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-[220px_1fr]">
        {/* Facettes */}
        <aside className="space-y-5">
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
                      <span className="ml-2 shrink-0 tabular-nums text-muted-foreground">
                        {entree.effectif}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </aside>

        {/* Résultats */}
        <section>
          {chargement && <p className="text-sm text-muted-foreground">Recherche…</p>}
          {erreur && !chargement && (
            <p className="rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {erreur}
            </p>
          )}
          {!chargement && !erreur && reponse && (
            <>
              <p className="mb-4 text-xs text-muted-foreground">
                {reponse.nb_resultats} fiche{reponse.nb_resultats === 1 ? "" : "s"}
                {requete && (
                  <>
                    {" "}pour « <span className="text-foreground/80">{requete}</span> »
                  </>
                )}{" "}
                · {reponse.duree_ms} ms · sources :{" "}
                {reponse.sources_actives.map((s) => LIBELLE_SOURCE[s] ?? s).join(", ") || "—"}
              </p>
              {reponse.sans_resultat && (
                <div className="rounded border border-border bg-background/60 px-4 py-6 text-center" data-testid="recherche-sans-resultat">
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
                        <span className="font-mono text-xs text-muted-foreground">
                          {fiche.code ?? "—"}
                        </span>
                        <h2 className="text-sm font-medium">{fiche.titre ?? "Sans titre"}</h2>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {[fiche.type_voile, fiche.client, fiche.bateau, fiche.annee, fiche.gamme]
                          .filter((v): v is string | number => v !== null && v !== "")
                          .join(" · ")}
                      </p>
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
