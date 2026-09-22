"use client"

// Panneau ASSISTANT SOURCÉ (lot I — plan v3.0 §11, Phase 4), intégré à
// l'écran Recherche : une question en français, une réponse courte, et des
// CITATIONS cliquables (fiche + champ ; quand la zone PDF existe, le lien
// ouvre /dossier/<code>?champ=… qui surligne la zone dans la visionneuse).
//
// Aucune réponse n'est générée : l'assistant est extractif côté backend —
// s'il ne trouve pas, il refuse (« je ne trouve pas dans les fiches ») et
// l'état l'affiche (sans_source / occupe / indisponible). Ce panneau n'invente
// jamais une interface de réponse : il rend le contrat du backend.

import { useCallback, useRef, useState } from "react"
import { Link2, Sparkles } from "lucide-react"
import { authedFetch } from "@/lib/authed-fetch"
import { cn } from "@/lib/utils"

export interface CitationAssistante {
  code_fiche: string | null
  champ: string
  libelle: string | null
  table_cible: string | null
  colonne_cible: string | null
  valeur: string
  valeur_normalisee: string | null
  page: number | null
  zone: { page: number; x0: number; y0: number; x1: number; y1: number } | null
  lien?: string
  rang?: number
}

export interface InterpretationAssistante {
  lecture: string
  reponse: string
  citations: CitationAssistante[]
}

export interface ReponseAssistante {
  question: string
  etat: "ok" | "ambigu" | "sans_source" | "occupe" | "indisponible"
  reponse: string
  citations: CitationAssistante[]
  interpretations: InterpretationAssistante[]
  pistes: string[]
  duree_ms: number
}

const LIBELLE_ETAT: Record<ReponseAssistante["etat"], string> = {
  ok: "Source",
  ambigu: "À préciser",
  sans_source: "Non trouvé dans les fiches",
  occupe: "Occupé — réessayez dans un instant",
  indisponible: "Indisponible",
}

const STYLE_ETAT: Record<ReponseAssistante["etat"], string> = {
  ok: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  ambigu: "border-amber-500/40 bg-amber-500/10 text-amber-300",
  sans_source: "border-border bg-background/60 text-muted-foreground",
  occupe: "border-amber-500/40 bg-amber-500/10 text-amber-300",
  indisponible: "border-red-500/40 bg-red-500/10 text-red-300",
}

function Citation({ citation }: { citation: CitationAssistante }) {
  const href = citation.lien ?? (citation.code_fiche ? `/dossier/${encodeURIComponent(citation.code_fiche)}` : null)
  const interieur = (
    <>
      <Link2 className="size-3 shrink-0" />
      <span className="truncate">
        {citation.code_fiche ?? "—"} · {citation.libelle ?? citation.champ} :{" "}
        <span className="font-medium text-foreground">{citation.valeur}</span>
      </span>
      {citation.zone ? (
        <span className="shrink-0 text-[10px] text-muted-foreground">
          PDF p.{(citation.page ?? citation.zone.page) + 1}
        </span>
      ) : null}
    </>
  )
  if (!href) {
    return <span className="flex items-center gap-1.5 text-xs text-muted-foreground">{interieur}</span>
  }
  return (
    <a
      href={href}
      className="flex max-w-full items-center gap-1.5 rounded px-1.5 py-1 text-xs text-muted-foreground hover:bg-accent/50 hover:text-foreground"
      title={`Ouvrir la fiche ${citation.code_fiche} sur la zone de ${citation.libelle ?? citation.champ}`}
    >
      {interieur}
    </a>
  )
}

export function AssistantPanneau() {
  const [saisie, setSaisie] = useState("")
  const [reponse, setReponse] = useState<ReponseAssistante | null>(null)
  const [chargement, setChargement] = useState(false)
  const [erreur, setErreur] = useState<string | null>(null)
  const questionRef = useRef(0)

  const demander = useCallback(async (question: string) => {
    const id = ++questionRef.current
    setChargement(true)
    setErreur(null)
    try {
      const res = await authedFetch("/api/assistant", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: question.trim() }),
      })
      const corps = await res.json().catch(() => ({}))
      if (id !== questionRef.current) return
      if (!res.ok && corps?.etat !== "indisponible") {
        throw new Error(String(corps?.detail ?? `HTTP ${res.status}`))
      }
      setReponse(corps as ReponseAssistante)
    } catch (e) {
      if (id !== questionRef.current) return
      setErreur(e instanceof Error ? e.message : "Question impossible pour le moment.")
    } finally {
      if (id === questionRef.current) setChargement(false)
    }
  }, [])

  return (
    <section
      className="rounded-lg border border-border bg-background/60 p-4"
      data-testid="assistant-panneau"
      aria-label="Assistant sourcé"
    >
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (saisie.trim()) void demander(saisie)
        }}
      >
        <Sparkles className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        <input
          value={saisie}
          onChange={(e) => setSaisie(e.target.value)}
          placeholder="Posez une question sur les fiches — ex. « quelle est la SLU de la fiche 7792-SO ? »"
          className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          data-testid="assistant-question"
          maxLength={500}
        />
        <button
          type="submit"
          disabled={!saisie.trim() || chargement}
          className="shrink-0 rounded bg-accent px-3 py-1 text-xs font-medium text-accent-foreground hover:opacity-90 disabled:opacity-40"
          data-testid="assistant-bouton"
        >
          Demander
        </button>
      </form>

      {chargement && (
        <p className="mt-3 text-sm text-muted-foreground" data-testid="assistant-chargement">
          Analyse des fiches…
        </p>
      )}
      {erreur && !chargement && (
        <p className="mt-3 rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300" data-testid="assistant-erreur">
          {erreur}
        </p>
      )}

      {reponse && !chargement && (
        <div className="mt-3 space-y-2" data-testid="assistant-resultat">
          <div className="flex items-center gap-2">
            <span
              className={cn("rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wide", STYLE_ETAT[reponse.etat])}
              data-testid="assistant-etat"
            >
              {LIBELLE_ETAT[reponse.etat]}
            </span>
            {reponse.etat !== "occupe" && reponse.etat !== "indisponible" && (
              <span className="text-[10px] text-muted-foreground">{reponse.duree_ms} ms</span>
            )}
          </div>

          <p className="text-sm" data-testid="assistant-reponse">
            {reponse.reponse}
          </p>

          {reponse.interpretations.length > 0 && (
            <ul className="space-y-2" data-testid="assistant-interpretations">
              {reponse.interpretations.map((interpretation) => (
                <li key={interpretation.lecture} className="rounded border border-border/70 px-3 py-2">
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{interpretation.lecture}</p>
                  <p className="mt-0.5 text-sm">{interpretation.reponse}</p>
                  {interpretation.citations.length > 0 && (
                    <ul className="mt-1 space-y-0.5">
                      {interpretation.citations.map((citation, i) => (
                        <li key={`${citation.code_fiche}-${citation.champ}-${i}`}>
                          <Citation citation={citation} />
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          )}

          {reponse.citations.length > 0 && (
            <ul className="space-y-0.5" data-testid="assistant-citations">
              {reponse.citations.map((citation, i) => (
                <li key={`${citation.code_fiche}-${citation.champ}-${i}`}>
                  <Citation citation={citation} />
                </li>
              ))}
            </ul>
          )}

          {reponse.pistes.length > 0 && (
            <div className="rounded border border-border/70 bg-background/40 px-3 py-2" data-testid="assistant-pistes">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Pistes (pas des réponses)</p>
              <ul className="mt-1 list-inside list-disc space-y-0.5 text-xs text-muted-foreground">
                {reponse.pistes.map((piste) => (
                  <li key={piste}>{piste}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
