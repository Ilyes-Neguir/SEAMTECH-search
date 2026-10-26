"use client"

// Écran CONFIANCE DE RÉUTILISATION (Lot 5, Fonction B) — indice de cohérence
// robuste (médiane/MAD/IQR) d'une voile candidate face aux voiles déjà
// connues pour son modèle de bateau et son type. Honnêteté d'abord :
//   * moins de 5 échantillons → « Données insuffisantes (N/5) », AUCUN % ;
//   * population hétérogène → aucun score, les statistiques restent visibles ;
//   * sous le seuil d'alerte (75) → le % s'affiche avec sa justification,
//     sans verdict positif (« à examiner »).
// Évaluer est réservé à l'approbateur et chaque évaluation est persistée.

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { AlertTriangle, Gauge, Info } from "lucide-react"
import { AccesRefuse } from "@/components/acces-refuse"
import { authedFetch } from "@/lib/authed-fetch"
import { useSession } from "@/lib/use-session"
import { estApprobateur, type Role } from "@/lib/roles"
import { cn } from "@/lib/utils"

interface ExtractionResume {
  id_extraction: number
  code_fiche: string | null
  nom_fichier_source: string | null
  type_voile: string | null
  modele_bateau_brut: string | null
  boat_model_id: number | null
  statut_liaison: string
  statut_plausibilite: string
}

interface DimensionJustifiee {
  champ: string
  n: number
  mediane: number | null
  q1: number | null
  q3: number | null
  iqr: number | null
  mad: number | null
  min: number | null
  max: number | null
  heterogene: boolean
  valeur_candidate: number | null
  ecart_pct_mediane: number | null
  z_robuste: number | null
  score_dim: number | null
}

interface Justification {
  n_population: number
  seuil_min_echantillons: number
  seuil_alerte: number
  dimensions: DimensionJustifiee[]
  materiaux: {
    part_meme_materiau: number | null
    detail: Array<{
      role: string
      niveau: number | null
      designation: string | null
      grammage_g_m2: number | null
      n_population_avec_role: number
      part_meme_materiau: number | null
    }>
  }
  sources: Array<{
    id_extraction: number | null
    code_fiche: string | null
    nom_fichier_source: string | null
    code_fiche_metier?: string | null
  }>
}

interface Evaluation {
  id_evaluation: number | null
  id_extraction: number
  boat_model_id: number
  type_voile: string
  n_echantillons: number
  score: number | null
  verdict: string
  libelle_verdict?: string
  justification: Justification
  cree_le?: string | null
}

interface Population {
  boat_model_id: number
  modele_bateau: string | null
  type_voile: string
  n_echantillons: number
  seuil_min_echantillons: number
  dimensions: Array<{
    champ: string
    n: number
    mediane: number | null
    q1: number | null
    q3: number | null
    iqr: number | null
    mad: number | null
    min: number | null
    max: number | null
  }>
  membres: Array<{ id_extraction: number; code_fiche: string | null; nom_fichier_source: string | null }>
}

const LIBELLES_DIMENSIONS: Record<string, string> = {
  slu_m: "SLU (m)",
  sle_m: "SLE (m)",
  sf_m: "SF (m)",
  shw_m: "SHW (m)",
  spa_m2: "Surface (m²)",
  tetiere_m: "Têtière (m)",
  poids_kg: "Poids (kg)",
}

const LIBELLES_VERDICT: Record<string, string> = {
  donnees_insuffisantes: "Données insuffisantes",
  heterogene: "Population hétérogène",
  a_examiner: "À examiner",
  compatible: "Cohérente avec la population",
}

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await authedFetch(url, init)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = (body as { detail?: unknown })?.detail
    const message =
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object" && "message" in detail
          ? String((detail as { message: unknown }).message)
          : `HTTP ${res.status}`
    throw Object.assign(new Error(message), { status: res.status, body })
  }
  return body as T
}

function fmt(valeur: number | null | undefined, decimales = 3): string {
  return valeur === null || valeur === undefined ? "—" : valeur.toFixed(decimales)
}

function BadgeVerdict({ evaluation }: { evaluation: Evaluation }) {
  const classes =
    evaluation.verdict === "compatible"
      ? "bg-emerald-100 text-emerald-900 border-emerald-300"
      : evaluation.verdict === "a_examiner"
        ? "bg-amber-100 text-amber-900 border-amber-300"
        : "bg-slate-100 text-slate-800 border-slate-300"
  return (
    <div className={cn("rounded-lg border p-4", classes)} role="status">
      <div className="text-xs font-semibold uppercase tracking-wide">
        {LIBELLES_VERDICT[evaluation.verdict] ?? evaluation.verdict}
      </div>
      <div className="mt-1 text-2xl font-bold">
        {evaluation.score === null
          ? evaluation.verdict === "donnees_insuffisantes"
            ? `Données insuffisantes (${evaluation.n_echantillons}/${evaluation.justification.seuil_min_echantillons})`
            : "Score non calculé"
          : `${evaluation.score} %`}
      </div>
      <p className="mt-1 text-sm">
        {evaluation.libelle_verdict ??
          (evaluation.score === null
            ? "Aucun pourcentage n'est fabriqué : la population ne permet pas de conclure."
            : "Indice de cohérence robuste (médiane/MAD/IQR) — pas une probabilité.")}
      </p>
      <p className="mt-1 text-xs opacity-80">
        Population de référence : {evaluation.n_echantillons} voile(s) du même modèle et du même type
        (seuil d'alerte : {evaluation.justification.seuil_alerte} %).
      </p>
    </div>
  )
}

function TableDimensions({ dimensions }: { dimensions: DimensionJustifiee[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b text-xs uppercase text-muted-foreground">
            <th className="py-2 pr-3">Dimension</th>
            <th className="py-2 pr-3">N</th>
            <th className="py-2 pr-3">Médiane</th>
            <th className="py-2 pr-3">Q1 – Q3</th>
            <th className="py-2 pr-3">IQR</th>
            <th className="py-2 pr-3">MAD</th>
            <th className="py-2 pr-3">Min – Max</th>
            <th className="py-2 pr-3">Candidate</th>
            <th className="py-2 pr-3">Écart à la médiane</th>
            <th className="py-2 pr-3">z robuste</th>
            <th className="py-2">Score</th>
          </tr>
        </thead>
        <tbody data-testid="confiance-dimensions">
          {dimensions.map((d) => (
            <tr key={d.champ} className="border-b last:border-0">
              <td className="py-2 pr-3 font-medium">
                {LIBELLES_DIMENSIONS[d.champ] ?? d.champ}
                {d.heterogene ? (
                  <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-semibold text-amber-900">
                    hétérogène
                  </span>
                ) : null}
              </td>
              <td className="py-2 pr-3">{d.n}</td>
              <td className="py-2 pr-3">{fmt(d.mediane)}</td>
              <td className="py-2 pr-3">
                {fmt(d.q1)} – {fmt(d.q3)}
              </td>
              <td className="py-2 pr-3">{fmt(d.iqr)}</td>
              <td className="py-2 pr-3">{fmt(d.mad)}</td>
              <td className="py-2 pr-3">
                {fmt(d.min)} – {fmt(d.max)}
              </td>
              <td className="py-2 pr-3 font-semibold">{fmt(d.valeur_candidate)}</td>
              <td className="py-2 pr-3">{d.ecart_pct_mediane === null ? "—" : `${d.ecart_pct_mediane.toFixed(2)} %`}</td>
              <td className="py-2 pr-3">{fmt(d.z_robuste, 2)}</td>
              <td className="py-2">{d.score_dim === null ? "—" : `${d.score_dim} %`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ConfianceAppInterne({ role }: { role: Role }) {
  const [extractions, setExtractions] = useState<ExtractionResume[]>([])
  const [filtre, setFiltre] = useState("")
  const [selection, setSelection] = useState<ExtractionResume | null>(null)
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null)
  const [historique, setHistorique] = useState<Evaluation[]>([])
  const [population, setPopulation] = useState<Population | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)
  const [occupe, setOccupe] = useState(false)
  const generation = useRef(0)
  const peutEvaluer = estApprobateur(role)

  const chargerExtractions = useCallback(async () => {
    try {
      const corps = await jsonFetch<{ extractions: ExtractionResume[] }>("/api/extractions-voile?limite=100")
      setExtractions(corps.extractions)
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "Liste des extractions illisible.")
    }
  }, [])

  useEffect(() => {
    void chargerExtractions()
  }, [chargerExtractions])

  const chargerContexte = useCallback(async (extraction: ExtractionResume) => {
    const jeton = ++generation.current
    setEvaluation(null)
    setHistorique([])
    setPopulation(null)
    setErreur(null)
    try {
      const histo = await jsonFetch<{ evaluations: Evaluation[] }>(
        `/api/confiance-reutilisation/evaluations?id_extraction=${extraction.id_extraction}`,
      )
      if (jeton !== generation.current) return
      setHistorique(histo.evaluations)
      if (histo.evaluations.length > 0) setEvaluation(histo.evaluations[0])
    } catch (e) {
      if (jeton === generation.current) setErreur(e instanceof Error ? e.message : "Historique illisible.")
    }
    if (extraction.boat_model_id !== null && extraction.type_voile) {
      try {
        const parametres = new URLSearchParams({
          boat_model_id: String(extraction.boat_model_id),
          type_voile: extraction.type_voile,
        })
        const pop = await jsonFetch<Population>(`/api/confiance-reutilisation/population?${parametres.toString()}`)
        if (jeton !== generation.current) return
        setPopulation(pop)
      } catch (e) {
        if (jeton === generation.current) setErreur(e instanceof Error ? e.message : "Population illisible.")
      }
    }
  }, [])

  const evaluer = useCallback(async () => {
    if (!selection) return
    setOccupe(true)
    setErreur(null)
    try {
      const resultat = await jsonFetch<Evaluation>("/api/confiance-reutilisation/evaluer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id_extraction: selection.id_extraction }),
      })
      setEvaluation(resultat)
      setHistorique((avant) => [resultat, ...avant])
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "Évaluation impossible.")
    } finally {
      setOccupe(false)
    }
  }, [selection])

  useEffect(() => {
    const parametre = new URLSearchParams(window.location.search).get("id")
    if (parametre && extractions.length > 0) {
      const trouvee = extractions.find((e) => e.id_extraction === Number(parametre))
      if (trouvee) {
        setSelection(trouvee)
        void chargerContexte(trouvee)
      }
    }
  }, [extractions, chargerContexte])

  const listeFiltree = useMemo(() => {
    const q = filtre.trim().toLowerCase()
    if (!q) return extractions
    return extractions.filter((e) =>
      [e.code_fiche, e.nom_fichier_source, e.type_voile, e.modele_bateau_brut]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(q)),
    )
  }, [extractions, filtre])

  return (
    <main className="mx-auto max-w-7xl space-y-4 p-4">
      <header className="flex items-center gap-2">
        <Gauge className="h-6 w-6" aria-hidden />
        <div>
          <h1 className="text-xl font-bold">Confiance de réutilisation</h1>
          <p className="text-sm text-muted-foreground">
            Indice de cohérence robuste (médiane / MAD / IQR) d’une voile face aux voiles du même modèle et du même
            type. Jamais de pourcentage sous 5 échantillons ni sur population hétérogène.
          </p>
        </div>
      </header>

      {erreur ? (
        <div className="flex items-start gap-2 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-900" role="alert">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>{erreur}</span>
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[340px_1fr]">
        <section className="space-y-3 rounded-lg border p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide">Voile candidate</h2>
          <input
            value={filtre}
            onChange={(e) => setFiltre(e.target.value)}
            placeholder="Filtrer (fiche, type, modèle…)"
            className="w-full rounded-md border px-3 py-2 text-sm"
            aria-label="Filtrer les extractions"
          />
          <ul className="max-h-[520px] space-y-1 overflow-y-auto" data-testid="confiance-liste">
            {listeFiltree.map((e) => {
              const liee = e.boat_model_id !== null
              return (
                <li key={e.id_extraction}>
                  <button
                    type="button"
                    onClick={() => {
                      setSelection(e)
                      void chargerContexte(e)
                    }}
                    className={cn(
                      "w-full rounded-md border px-3 py-2 text-left text-sm",
                      selection?.id_extraction === e.id_extraction ? "border-primary bg-primary/10" : "hover:bg-muted",
                    )}
                  >
                    <span className="font-semibold">{e.code_fiche ?? `#${e.id_extraction}`}</span>
                    <span className="ml-2">{e.type_voile ?? "type inconnu"}</span>
                    <span className="block text-xs text-muted-foreground">
                      {e.modele_bateau_brut ?? "modèle non indiqué"} · {liee ? "liée au référentiel" : "non liée"}
                    </span>
                  </button>
                </li>
              )
            })}
            {listeFiltree.length === 0 ? (
              <li className="text-sm text-muted-foreground">Aucune extraction à afficher.</li>
            ) : null}
          </ul>
        </section>

        <section className="space-y-4">
          {!selection ? (
            <div className="flex items-start gap-2 rounded-lg border p-4 text-sm text-muted-foreground">
              <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              <p>Sélectionnez une voile candidate liée au référentiel bateau pour consulter sa population et ses évaluations.</p>
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-3 rounded-lg border p-4">
                <div className="min-w-0 flex-1">
                  <div className="font-semibold">{selection.code_fiche ?? `#${selection.id_extraction}`}</div>
                  <div className="text-sm text-muted-foreground">
                    {selection.type_voile ?? "type inconnu"} · {selection.nom_fichier_source ?? "fichier inconnu"}
                  </div>
                </div>
                {selection.boat_model_id === null ? (
                  <p className="text-sm text-amber-800">
                    Modèle de bateau non résolu : aucune population de référence. Liez la voile dans l’écran
                    Extractions.
                  </p>
                ) : peutEvaluer ? (
                  <button
                    type="button"
                    onClick={() => void evaluer()}
                    disabled={occupe}
                    className="rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50"
                  >
                    {occupe ? "Évaluation…" : "Évaluer la confiance"}
                  </button>
                ) : (
                  <p className="text-sm text-muted-foreground">Seul un approbateur peut lancer et persister une évaluation.</p>
                )}
              </div>

              {evaluation ? (
                <div className="space-y-4" data-testid="confiance-resultat">
                  <BadgeVerdict evaluation={evaluation} />
                  <div className="rounded-lg border p-4">
                    <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide">
                      Justification — dispersion par dimension
                    </h3>
                    <TableDimensions dimensions={evaluation.justification.dimensions} />
                    <p className="mt-3 text-sm">
                      Matériaux concordants :{" "}
                      {evaluation.justification.materiaux.part_meme_materiau === null
                        ? "non calculable (rôles absents de la population)"
                        : `${Math.round(evaluation.justification.materiaux.part_meme_materiau * 100)} % en moyenne`}
                    </p>
                    <details className="mt-2 text-sm">
                      <summary className="cursor-pointer font-medium">
                        Fiches sources de la population ({evaluation.justification.sources.length})
                      </summary>
                      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-muted-foreground">
                        {evaluation.justification.sources.map((s) => (
                          <li key={s.id_extraction ?? s.code_fiche ?? ""}>
                            {s.code_fiche_metier ? (
                              <a
                                href={`/fiches/${encodeURIComponent(s.code_fiche_metier)}`}
                                className="font-medium text-primary underline-offset-2 hover:underline"
                              >
                                {s.code_fiche ?? `#${s.id_extraction}`}
                              </a>
                            ) : (
                              (s.code_fiche ?? `#${s.id_extraction}`)
                            )}{" "}
                            — {s.nom_fichier_source ?? "fichier inconnu"}
                          </li>
                        ))}
                      </ul>
                    </details>
                  </div>
                </div>
              ) : (
                <div className="rounded-lg border p-4 text-sm text-muted-foreground">
                  Aucune évaluation persistée pour cette candidate pour le moment.
                </div>
              )}

              {population ? (
                <div className="rounded-lg border p-4">
                  <h3 className="mb-1 text-sm font-semibold uppercase tracking-wide">
                    Population {population.modele_bateau ?? ""} · {population.type_voile}
                  </h3>
                  <p className="mb-2 text-sm text-muted-foreground">
                    {population.n_echantillons} voile(s) (minimum requis pour un score :{" "}
                    {population.seuil_min_echantillons}). La candidate fait partie de cette vue ; elle est exclue de
                    son propre échantillon lors de l’évaluation.
                  </p>
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm">
                      <thead>
                        <tr className="border-b text-xs uppercase text-muted-foreground">
                          <th className="py-2 pr-3">Dimension</th>
                          <th className="py-2 pr-3">N</th>
                          <th className="py-2 pr-3">Médiane</th>
                          <th className="py-2 pr-3">IQR</th>
                          <th className="py-2">Min – Max</th>
                        </tr>
                      </thead>
                      <tbody data-testid="confiance-population">
                        {population.dimensions.map((d) => (
                          <tr key={d.champ} className="border-b last:border-0">
                            <td className="py-2 pr-3 font-medium">{LIBELLES_DIMENSIONS[d.champ] ?? d.champ}</td>
                            <td className="py-2 pr-3">{d.n}</td>
                            <td className="py-2 pr-3">{fmt(d.mediane)}</td>
                            <td className="py-2 pr-3">{fmt(d.iqr)}</td>
                            <td className="py-2">
                              {fmt(d.min)} – {fmt(d.max)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : null}

              {historique.length > 0 ? (
                <div className="rounded-lg border p-4">
                  <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide">Évaluations persistées</h3>
                  <ul className="space-y-1 text-sm" data-testid="confiance-historique">
                    {historique.map((h) => (
                      <li key={h.id_evaluation ?? ""}>
                        <button
                          type="button"
                          onClick={() => setEvaluation(h)}
                          className="w-full rounded-md border px-3 py-2 text-left hover:bg-muted"
                        >
                          {LIBELLES_VERDICT[h.verdict] ?? h.verdict} ·{" "}
                          {h.score === null ? "sans score" : `${h.score} %`} · N={h.n_echantillons}
                          {h.cree_le ? ` · ${new Date(h.cree_le).toLocaleString("fr-FR")}` : ""}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </>
          )}
        </section>
      </div>
    </main>
  )
}

export function ConfianceApp() {
  const { session, chargement } = useSession()
  if (chargement) {
    return <main className="p-6 text-sm text-muted-foreground">Vérification de votre session…</main>
  }
  if (session.mode !== "nominatif") {
    return (
      <AccesRefuse message="La confiance de réutilisation lit des extractions nominatives : connectez-vous avec un compte nominatif (le compte de secours ne suffit pas)." />
    )
  }
  return <ConfianceAppInterne role={session.role} />
}
