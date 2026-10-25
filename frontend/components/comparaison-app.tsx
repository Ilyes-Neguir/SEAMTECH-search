"use client"

// Écran COMPARAISON (Lot 4b, Fonction A) — deux voiles côte à côte, bloc par
// bloc : identification, dimensions (écarts en cm et en %), matériaux,
// qualité. « Même modèle » n'est affirmé que si le modèle canonique ET le
// type coïncident ; sinon l'écran le dit franchement. Aucun verdict de
// réutilisation ici — des écarts et des preuves (la confiance est le Lot 5).

import { useCallback, useEffect, useRef, useState } from "react"
import { AlertTriangle, CheckCircle2, Scale } from "lucide-react"
import { AccesRefuse } from "@/components/acces-refuse"
import { authedFetch } from "@/lib/authed-fetch"
import { useSession } from "@/lib/use-session"
import { cn } from "@/lib/utils"

interface ResumeExtraction {
  id_extraction: number
  code_fiche: string | null
  nom_fichier_source: string | null
  type_voile: string | null
  gamme: string | null
  modele_bateau_brut: string | null
  boat_model_id: number | null
  modele_lie?: string | { nom_canonique: string } | null
  statut_liaison: string
}

interface LigneIdentification {
  champ: string
  libelle: string
  a: string | null
  b: string | null
  identique: boolean | null
}

interface LigneDimension {
  champ: string
  libelle: string
  unite: string
  a: number | null
  b: number | null
  delta: number | null
  delta_cm: number | null
  delta_pct: number | null
  etat: string
}

interface VueMateriau {
  designation: string | null
  grammage_g_m2: number | null
}

interface LigneMateriau {
  role: string
  niveau: number | null
  a: VueMateriau | null
  b: VueMateriau | null
  etat: string
  delta_grammage_pct: number | null
}

interface LigneQualite {
  champ: string
  libelle: string
  a: string | number | null
  b: string | number | null
}

interface Comparaison {
  a: ResumeExtraction
  b: ResumeExtraction
  meme_modele_bateau: boolean
  meme_type_voile: boolean
  comparable: boolean
  avertissements: string[]
  blocs: {
    identification: LigneIdentification[]
    dimensions: LigneDimension[]
    materiaux: LigneMateriau[]
    qualite: LigneQualite[]
  }
  synthese: {
    dimensions_communes: number
    dimensions_identiques: number
    ecart_max_pct: number | null
    ecart_max_champ: string | null
  }
}

interface CandidatComparaison {
  extraction: ResumeExtraction
  dimensions_communes: number
}

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await authedFetch(url, init)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = body?.detail
    throw new Error(typeof detail === "string" ? detail : `HTTP ${res.status}`)
  }
  return body as T
}

function nomModele(resume: ResumeExtraction | null | undefined): string {
  if (!resume) return "—"
  if (typeof resume.modele_lie === "string" && resume.modele_lie) return resume.modele_lie
  if (resume.modele_lie && typeof resume.modele_lie === "object") return resume.modele_lie.nom_canonique
  return resume.modele_bateau_brut ?? "bateau non lu"
}

function fmt(nombre: number | null, decimales = 2): string {
  if (nombre === null || nombre === undefined) return "—"
  return nombre.toLocaleString("fr-FR", {
    minimumFractionDigits: decimales,
    maximumFractionDigits: decimales,
  })
}

function fmtSigne(nombre: number | null, decimales = 1): string {
  if (nombre === null || nombre === undefined) return "—"
  const signe = nombre > 0 ? "+" : ""
  return `${signe}${fmt(nombre, decimales)}`
}

function classeEcart(pct: number | null): string {
  if (pct === null) return "bg-muted text-muted-foreground"
  const absolu = Math.abs(pct)
  if (absolu <= 1) return "bg-emerald-500/20 text-emerald-800 dark:text-emerald-200"
  if (absolu <= 5) return "bg-amber-500/20 text-amber-800 dark:text-amber-200"
  return "bg-red-500/20 text-red-800 dark:text-red-200"
}

function ListeChoix({
  titre,
  cote,
  elements,
  idChoisi,
  onChoisir,
  testId,
}: {
  titre: string
  cote: "a" | "b"
  elements: ResumeExtraction[]
  idChoisi: number | null
  onChoisir: (id: number) => void
  testId: string
}) {
  return (
    <div>
      <h3 className="mb-1 text-xs font-semibold">{titre}</h3>
      <ul className="max-h-44 overflow-auto rounded border border-border" data-testid={testId}>
        {elements.length === 0 && <li className="p-2 text-xs text-muted-foreground">Aucune extraction.</li>}
        {elements.map((element) => (
          <li key={element.id_extraction}>
            <button
              type="button"
              onClick={() => onChoisir(element.id_extraction)}
              className={cn(
                "block w-full border-b border-border/50 px-2 py-1.5 text-left text-xs hover:bg-accent/40",
                idChoisi === element.id_extraction && "bg-accent",
              )}
              data-testid="extraction-choix"
              data-cote={cote}
              data-id={element.id_extraction}
            >
              <span className="block truncate font-mono font-semibold">
                {element.code_fiche ?? `#${element.id_extraction}`}
              </span>
              <span className="block truncate text-muted-foreground">
                {element.type_voile ?? "type non lu"} · {nomModele(element)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

function ComparaisonAppInterne() {
  const [extractions, setExtractions] = useState<ResumeExtraction[]>([])
  const [rechercheA, setRechercheA] = useState("")
  const [idA, setIdA] = useState<number | null>(null)
  const [idB, setIdB] = useState<number | null>(null)
  const [candidats, setCandidats] = useState<CandidatComparaison[]>([])
  const [raisonSansCandidat, setRaisonSansCandidat] = useState<string | null>(null)
  const [comparaison, setComparaison] = useState<Comparaison | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)
  const generation = useRef(0)

  const chargerListe = useCallback(async () => {
    try {
      const corps = await jsonFetch<{ extractions: ResumeExtraction[] }>("/api/extractions-voile?limite=100")
      setExtractions(corps.extractions)
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "Extractions illisibles.")
    }
  }, [])

  const chargerCandidats = useCallback(async (idExtraction: number) => {
    try {
      const corps = await jsonFetch<{
        candidats: CandidatComparaison[]
        raison: string | null
      }>(`/api/comparaison-voiles/candidats?id_extraction=${idExtraction}`)
      setCandidats(corps.candidats)
      setRaisonSansCandidat(corps.raison)
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "Candidats illisibles.")
    }
  }, [])

  const chargerComparaison = useCallback(async (a: number, b: number) => {
    const jeton = ++generation.current
    try {
      const corps = await jsonFetch<Comparaison>(`/api/comparaison-voiles?a=${a}&b=${b}`)
      if (jeton !== generation.current) return
      setComparaison(corps)
      setErreur(null)
    } catch (e) {
      if (jeton === generation.current) {
        setComparaison(null)
        setErreur(e instanceof Error ? e.message : "Comparaison refusée.")
      }
    }
  }, [])

  useEffect(() => {
    void chargerListe()
    const parametres = new URLSearchParams(window.location.search)
    const aInitial = Number(parametres.get("a")) || null
    const bInitial = Number(parametres.get("b")) || null
    if (aInitial) {
      setIdA(aInitial)
      void chargerCandidats(aInitial)
    }
    if (bInitial) setIdB(bInitial)
    if (aInitial && bInitial && aInitial !== bInitial) void chargerComparaison(aInitial, bInitial)
  }, [chargerListe, chargerCandidats, chargerComparaison])

  function choisirA(id: number) {
    setIdA(id)
    setComparaison(null)
    setIdB(null)
    void chargerCandidats(id)
  }

  function choisirB(id: number) {
    setIdB(id)
    if (idA !== null && id !== idA) void chargerComparaison(idA, id)
  }

  const listeA = extractions.filter((e) => {
    if (!rechercheA.trim()) return true
    const texte = `${e.code_fiche ?? ""} ${e.type_voile ?? ""} ${e.modele_bateau_brut ?? ""}`.toLowerCase()
    return texte.includes(rechercheA.trim().toLowerCase())
  })
  const listeBToutes = extractions.filter((e) => e.id_extraction !== idA)

  return (
    <div className="mx-auto min-h-[calc(100vh-3.5rem)] max-w-6xl p-4">
      <header className="mb-3 flex items-center gap-2">
        <Scale className="size-5" aria-hidden />
        <div>
          <h1 className="text-base font-semibold">Comparaison de voiles — bloc par bloc</h1>
          <p className="text-xs text-muted-foreground">
            Écarts calculés B − A sur les valeurs canoniques vérifiées (corrections d’approbateur
            incluses). Lecture seule : aucun verdict de réutilisation ici.
          </p>
        </div>
      </header>

      {erreur && (
        <div className="mb-3 rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive" role="alert" data-testid="erreur-comparaison">
          {erreur}
        </div>
      )}

      <section className="grid gap-4 rounded border border-border p-3 md:grid-cols-2" data-testid="selecteurs">
        <div>
          <input
            value={rechercheA}
            onChange={(e) => setRechercheA(e.target.value)}
            placeholder="Filtrer la voile A (code, type, bateau)…"
            className="mb-2 w-full rounded border border-border bg-input px-2 py-1 text-xs"
            data-testid="recherche-a"
          />
          <ListeChoix titre="Voile A (référence)" cote="a" elements={listeA} idChoisi={idA} onChoisir={choisirA} testId="liste-a" />
        </div>
        <div className="space-y-3">
          <div>
            <h3 className="mb-1 text-xs font-semibold">Voile B — même modèle + même type</h3>
            {idA === null && <p className="text-xs text-muted-foreground">Choisissez d’abord la voile A.</p>}
            {idA !== null && raisonSansCandidat === "non_liee" && (
              <p className="text-xs text-amber-700 dark:text-amber-300" data-testid="raison-sans-candidat">
                La voile A n’est pas liée à un modèle canonique : aucun candidat « même modèle »
                garanti. Liez-la dans l’écran Extractions, ou choisissez B librement ci-dessous
                (comparaison indicative).
              </p>
            )}
            {idA !== null && raisonSansCandidat !== "non_liee" && candidats.length === 0 && (
              <p className="text-xs text-muted-foreground">Aucune autre extraction du même modèle et du même type pour l’instant.</p>
            )}
            <ul className="space-y-1" data-testid="candidats-b">
              {candidats.map((candidat) => (
                <li key={candidat.extraction.id_extraction}>
                  <button
                    type="button"
                    onClick={() => choisirB(candidat.extraction.id_extraction)}
                    className={cn(
                      "block w-full rounded border border-border px-2 py-1.5 text-left text-xs hover:bg-accent/40",
                      idB === candidat.extraction.id_extraction && "bg-accent",
                    )}
                    data-testid="candidat-b"
                    data-id={candidat.extraction.id_extraction}
                  >
                    <span className="font-mono font-semibold">
                      {candidat.extraction.code_fiche ?? `#${candidat.extraction.id_extraction}`}
                    </span>{" "}
                    <span className="text-muted-foreground">
                      {candidat.extraction.type_voile ?? ""} · {candidat.dimensions_communes} dimension(s) commune(s)
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <ListeChoix titre="Voile B — toutes les extractions" cote="b" elements={listeBToutes} idChoisi={idB} onChoisir={choisirB} testId="liste-b" />
        </div>
      </section>

      {comparaison && (
        <section className="mt-4 space-y-4" data-testid="resultat-comparaison">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={cn(
                "flex items-center gap-1 rounded px-2 py-1 text-xs font-semibold",
                comparaison.comparable
                  ? "bg-emerald-500/20 text-emerald-800 dark:text-emerald-200"
                  : "bg-amber-500/20 text-amber-800 dark:text-amber-200",
              )}
              data-testid="badge-comparable"
              data-comparable={comparaison.comparable ? "oui" : "non"}
            >
              {comparaison.comparable ? <CheckCircle2 className="size-3.5" /> : <AlertTriangle className="size-3.5" />}
              {comparaison.comparable ? "Même modèle de bateau, même type de voile" : "Comparaison indicative (pas même modèle + type)"}
            </span>
            <span className="text-xs text-muted-foreground" data-testid="synthese">
              {comparaison.synthese.dimensions_communes} dimension(s) commune(s)
              {comparaison.synthese.ecart_max_pct !== null
                ? ` · écart max ${fmtSigne(comparaison.synthese.ecart_max_pct)} % (${comparaison.synthese.ecart_max_champ})`
                : ""}
            </span>
          </div>

          {comparaison.avertissements.length > 0 && (
            <ul className="space-y-1" data-testid="avertissements-comparaison">
              {comparaison.avertissements.map((texte, i) => (
                <li key={i} className="flex items-start gap-1 rounded bg-amber-500/10 px-2 py-1 text-[11px]">
                  <AlertTriangle className="mt-0.5 size-3 shrink-0" aria-hidden /> {texte}
                </li>
              ))}
            </ul>
          )}

          <div className="overflow-x-auto rounded border border-border" data-testid="bloc-dimensions">
            <table className="w-full text-xs" data-testid="tableau-dimensions">
              <thead>
                <tr className="border-b border-border bg-muted/50 text-left">
                  <th className="px-2 py-1.5">Dimension</th>
                  <th className="px-2 py-1.5">A — {comparaison.a.code_fiche ?? `#${comparaison.a.id_extraction}`}</th>
                  <th className="px-2 py-1.5">B — {comparaison.b.code_fiche ?? `#${comparaison.b.id_extraction}`}</th>
                  <th className="px-2 py-1.5">Δ (B − A)</th>
                  <th className="px-2 py-1.5">Δ cm</th>
                  <th className="px-2 py-1.5">Δ % / A</th>
                </tr>
              </thead>
              <tbody>
                {comparaison.blocs.dimensions.map((ligne) => (
                  <tr key={ligne.champ} className="border-b border-border/40" data-testid="ligne-dimension" data-champ={ligne.champ} data-etat={ligne.etat}>
                    <td className="px-2 py-1.5 font-medium">{ligne.libelle}</td>
                    <td className="px-2 py-1.5 tabular-nums">{fmt(ligne.a)} {ligne.a !== null ? ligne.unite : ""}</td>
                    <td className="px-2 py-1.5 tabular-nums">{fmt(ligne.b)} {ligne.b !== null ? ligne.unite : ""}</td>
                    <td className="px-2 py-1.5 tabular-nums">{fmtSigne(ligne.delta, 3)} {ligne.delta !== null ? ligne.unite : ""}</td>
                    <td className="px-2 py-1.5 tabular-nums" data-testid={`delta-cm-${ligne.champ}`}>{ligne.delta_cm !== null ? `${fmtSigne(ligne.delta_cm)} cm` : "—"}</td>
                    <td className="px-2 py-1.5">
                      <span className={cn("rounded px-1.5 py-0.5 tabular-nums", classeEcart(ligne.delta_pct))} data-testid={`delta-pct-${ligne.champ}`}>
                        {ligne.delta_pct !== null ? `${fmtSigne(ligne.delta_pct)} %` : "—"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="-mt-2 text-[11px] text-muted-foreground">
            Couleurs des écarts (affichage seulement) : ≤ 1 % vert, ≤ 5 % ambre, au-delà rouge.
            Un « — » signifie valeur non lue ou non corrigée : jamais un écart inventé.
          </p>

          <div className="grid gap-4 md:grid-cols-3">
            <div className="rounded border border-border p-3" data-testid="bloc-identification">
              <h3 className="mb-2 text-xs font-semibold">Identification</h3>
              <ul className="space-y-1 text-xs">
                {comparaison.blocs.identification.map((ligne) => (
                  <li key={ligne.champ} className="flex justify-between gap-2">
                    <span className="text-muted-foreground">{ligne.libelle}</span>
                    <span className="text-right">
                      {ligne.a ?? "—"} <span className="text-muted-foreground">/</span> {ligne.b ?? "—"}
                      {ligne.identique !== null && (
                        <span className={cn("ml-1 font-semibold", ligne.identique ? "text-emerald-600" : "text-amber-600")}>
                          {ligne.identique ? "=" : "≠"}
                        </span>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="rounded border border-border p-3" data-testid="bloc-materiaux">
              <h3 className="mb-2 text-xs font-semibold">Matériaux</h3>
              {comparaison.blocs.materiaux.length === 0 && (
                <p className="text-xs text-muted-foreground">Aucun matériau structuré des deux côtés.</p>
              )}
              <ul className="space-y-1 text-xs">
                {comparaison.blocs.materiaux.map((ligne) => (
                  <li key={`${ligne.role}-${ligne.niveau ?? 0}`} className="border-b border-border/40 pb-1">
                    <span className="font-medium">{ligne.role}{ligne.niveau ? ` ${ligne.niveau}` : ""}</span>
                    <span className="block text-muted-foreground">
                      A : {ligne.a?.designation ?? "—"} · B : {ligne.b?.designation ?? "—"}
                      {ligne.delta_grammage_pct !== null && ` · grammage ${fmtSigne(ligne.delta_grammage_pct)} %`}
                      {ligne.etat !== "commun" && (ligne.etat === "a_seul" ? " · présent en A seul" : " · présent en B seul")}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="rounded border border-border p-3" data-testid="bloc-qualite">
              <h3 className="mb-2 text-xs font-semibold">Qualité & traçabilité</h3>
              <ul className="space-y-1 text-xs">
                {comparaison.blocs.qualite.map((ligne) => (
                  <li key={ligne.champ} className="flex justify-between gap-2">
                    <span className="text-muted-foreground">{ligne.libelle}</span>
                    <span className="text-right tabular-nums">
                      {ligne.a ?? "—"} <span className="text-muted-foreground">/</span> {ligne.b ?? "—"}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>
      )}
    </div>
  )
}

export function ComparaisonApp() {
  const { session, chargement } = useSession()
  if (chargement) {
    return <main className="p-6 text-sm text-muted-foreground">Vérification de votre session…</main>
  }
  if (session.mode !== "nominatif") {
    return (
      <AccesRefuse message="La comparaison de voiles lit des extractions nominatives : connectez-vous avec un compte nominatif (le compte de secours ne suffit pas)." />
    )
  }
  return <ComparaisonAppInterne />
}
