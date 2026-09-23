"use client"

// Écran VALIDATION (lot D, le cœur métier) — file triée par confiance
// croissante, champs avec valeur brute ET normalisée côte à côte, correction
// inline, surlignage dans le PDF, validation en lot SANS jamais écrire
// « valide » sans décision explicite (RG3). Le verrou de calibration (409)
// s'affiche avec sa sortie et la case d'acquittement explicite.

import { useCallback, useEffect, useMemo, useState } from "react"
import { Check, ChevronRight, CircleX, Lock, RefreshCw, RotateCcw, Undo2 } from "lucide-react"
import { PalierBadge } from "@/components/palier-badge"
import { PdfViewer, type ZoneASurligner } from "@/components/pdf-viewer"
import { authedFetch } from "@/lib/authed-fetch"
import { palierDeChamp, type ChampExtrait, type FicheFileEntry, type PiecesDeFiche } from "@/lib/fiche"
import { cn } from "@/lib/utils"

const UTILISATEUR = "operateur-atelier" // identité de session (traçabilité du journal)

// Lot L.1 — un lien de doublon, tel que le rend `GET /fiches/{code}/doublons`.
// Affiché AVANT la validation : « Doublon exact de CODE (sha256) » ou
// « Doublon probable de CODE (score 0.xx) ». Aucun bouton « fusionner » : la
// décision reste humaine, l'écran ne fait que prévenir.
export interface LienDoublon {
  id_lien: number
  type: "doublon_exact" | "doublon_probable" | string
  score: number | null
  code_autre: string
  statut_autre: string
}

function libelleDoublon(lien: LienDoublon): string {
  if (lien.type === "doublon_exact") return `Doublon exact de ${lien.code_autre}`
  return `Doublon probable de ${lien.code_autre}`
}

function detailDoublon(lien: LienDoublon): string {
  if (lien.type === "doublon_exact") return "même fichier (empreinte SHA-256 identique)"
  return typeof lien.score === "number" ? `score ${lien.score.toFixed(2)}` : "score non calculé"
}

function BandeauDoublon({ liens }: { liens: LienDoublon[] }) {
  if (liens.length === 0) return null
  return (
    <div
      className="mt-1 rounded border border-amber-500/50 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-900 dark:text-amber-200"
      data-testid="bandeau-doublon"
    >
      {liens.map((lien) => (
        <div key={lien.id_lien}>
          <span className="font-semibold">{libelleDoublon(lien)}</span>{" "}
          <span className="text-muted-foreground">({detailDoublon(lien)})</span>
        </div>
      ))}
      <div className="text-muted-foreground">À arbitrer avant validation — aucune fusion automatique.</div>
    </div>
  )
}

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await authedFetch(url, init)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw Object.assign(new Error(String(body?.detail ?? `HTTP ${res.status}`)), { status: res.status, body })
  return body as T
}

export function ValidationApp() {
  const [file, setFile] = useState<FicheFileEntry[]>([])
  const [filtreGabarit, setFiltreGabarit] = useState("")
  const [codeActif, setCodeActif] = useState<string | null>(null)
  const [champs, setChamps] = useState<ChampExtrait[]>([])
  const [zone, setZone] = useState<ZoneASurligner | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)
  const [motifRejet, setMotifRejet] = useState("")
  const [acquittement, setAcquittement] = useState(false)
  const [selection, setSelection] = useState<Set<string>>(new Set())
  const [occupe, setOccupe] = useState(false)

  const chargerFile = useCallback(async () => {
    try {
      const params = filtreGabarit ? `?gabarit=${encodeURIComponent(filtreGabarit)}` : ""
      setFile(await jsonFetch<FicheFileEntry[]>(`/api/validation/file${params}`))
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "File de validation indisponible.")
    }
  }, [filtreGabarit])

  useEffect(() => {
    chargerFile()
  }, [chargerFile])

  // Lot L.1 — liens de doublon de TOUTE la file, en une requête. Un échec ici
  // ne doit jamais empêcher de valider : le bandeau est une AIDE, pas une
  // condition. On retombe donc sur une carte vide, sans message d'erreur.
  const [doublons, setDoublons] = useState<Record<string, LienDoublon[]>>({})
  useEffect(() => {
    const codes = file.map((entree) => entree.code)
    if (codes.length === 0) {
      setDoublons({})
      return
    }
    let annule = false
    jsonFetch<{ par_code: Record<string, LienDoublon[]> }>("/api/validation/doublons", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ codes }),
    })
      .then((corps) => {
        if (!annule) setDoublons(corps.par_code ?? {})
      })
      .catch(() => {
        if (!annule) setDoublons({})
      })
    return () => {
      annule = true
    }
  }, [file])

  useEffect(() => {
    if (!codeActif && file.length > 0) setCodeActif(file[0].code)
  }, [file, codeActif])

  const [cheminPdf, setCheminPdf] = useState<string | null>(null)
  useEffect(() => {
    if (!codeActif) return
    setMotifRejet("")
    setZone(null)
    jsonFetch<ChampExtrait[]>(`/api/fiches/${encodeURIComponent(codeActif)}/champs`)
      .then(setChamps)
      .catch((e) => setErreur(e instanceof Error ? e.message : "Champs indisponibles."))
    jsonFetch<PiecesDeFiche>(`/api/fiches/${encodeURIComponent(codeActif)}/pieces`)
      .then((corps) => setCheminPdf(corps.pdf_source ?? corps.fichier_source))
      .catch(() => setCheminPdf(null))
  }, [codeActif])

  async function corriger(champ: ChampExtrait, valeur: string) {
    if (!codeActif) return
    setOccupe(true)
    setErreur(null)
    try {
      await jsonFetch(`/api/fiches/${encodeURIComponent(codeActif)}/corriger`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ champ: champ.champ, valeur, utilisateur: UTILISATEUR, rang: champ.rang }),
      })
      setMessage(`Champ « ${champ.champ} » corrigé — verrou RG11 armé (une valeur corrigée n'est plus écrasée).`)
      setChamps(await jsonFetch(`/api/fiches/${encodeURIComponent(codeActif)}/champs`))
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "Correction refusée.")
    } finally {
      setOccupe(false)
    }
  }

  async function action(actionName: "valider" | "rejeter" | "rouvrir", extra: Record<string, unknown> = {}) {
    if (!codeActif) return
    setOccupe(true)
    setErreur(null)
    try {
      await jsonFetch(`/api/fiches/${encodeURIComponent(codeActif)}/${actionName}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ utilisateur: UTILISATEUR, ...extra }),
      })
      setMessage(`Fiche ${codeActif} : ${actionName} enregistré au journal.`)
      await chargerFile()
      setCodeActif(null)
      setChamps([])
    } catch (e) {
      setErreur(e instanceof Error ? e.message : `Action ${actionName} refusée.`)
    } finally {
      setOccupe(false)
    }
  }

  async function validerEnLot() {
    setOccupe(true)
    setErreur(null)
    try {
      const corps = await jsonFetch<{ nb_validees: number; nb_ignorees: number }>("/api/validation/lot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ codes: [...selection], utilisateur: UTILISATEUR, acquittement_humain: acquittement }),
      })
      setMessage(`Validation en lot : ${corps.nb_validees} validée(s), ${corps.nb_ignorees} ignorée(s).`)
      setSelection(new Set())
      setAcquittement(false)
      await chargerFile()
    } catch (e) {
      const err = e as Error & { status?: number }
      setErreur(err.status === 409 ? `VERROU DE CALIBRATION — ${err.message}` : err.message)
    } finally {
      setOccupe(false)
    }
  }

  function basculerSelection(code: string) {
    setSelection((s) => {
      const copie = new Set(s)
      if (copie.has(code)) copie.delete(code)
      else copie.add(code)
      return copie
    })
  }

  return (
    <div className="flex h-[calc(100vh-3.5rem)] min-h-0 flex-col" data-testid="validation-app">
      {(message || erreur) && (
        <div
          className={cn(
            "flex items-start gap-2 border-b px-4 py-2 text-xs",
            erreur ? "border-destructive/40 bg-destructive/10 text-destructive" : "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
          )}
          data-testid={erreur ? "message-erreur" : "message-ok"}
        >
          <span className="flex-1">{erreur ?? message}</span>
          <button type="button" onClick={() => { setMessage(null); setErreur(null) }}>
            <CircleX className="size-3.5" />
          </button>
        </div>
      )}
      <div className="grid min-h-0 flex-1 grid-cols-[22rem_minmax(0,1fr)_minmax(0,1.1fr)]">
        {/* file de validation */}
        <aside className="flex min-h-0 flex-col border-r border-border">
          <header className="flex items-center justify-between border-b border-border p-3">
            <h2 className="text-sm font-semibold">File de validation</h2>
            <button type="button" onClick={chargerFile} className="text-muted-foreground hover:text-foreground" aria-label="Rafraîchir">
              <RefreshCw className="size-4" />
            </button>
          </header>
          <div className="border-b border-border p-3">
            <input
              value={filtreGabarit}
              onChange={(e) => setFiltreGabarit(e.target.value)}
              placeholder="Filtrer par gabarit…"
              className="w-full rounded border border-border bg-input px-2 py-1 text-xs"
              data-testid="filtre-gabarit"
            />
          </div>
          <ul className="min-h-0 flex-1 overflow-auto" data-testid="file-validation">
            {file.map((entree) => (
              <li key={entree.code}>
                <div
                  className={cn(
                    "flex w-full cursor-pointer items-center gap-2 border-b border-border/60 px-3 py-2 text-left text-xs hover:bg-accent/40",
                    codeActif === entree.code && "bg-accent",
                  )}
                >
                  <input
                    type="checkbox"
                    checked={selection.has(entree.code)}
                    onChange={() => basculerSelection(entree.code)}
                    aria-label={`Sélectionner ${entree.code} pour la validation en lot`}
                  />
                  <button type="button" className="min-w-0 flex-1" onClick={() => setCodeActif(entree.code)}>
                    <span className="block truncate font-mono font-semibold" data-testid="code-fiche">
                      {entree.code}
                    </span>
                    <span className="block truncate text-muted-foreground">{entree.titre}</span>
                    <span className="mt-1 flex flex-wrap gap-1" aria-label="Comptes par palier">
                      {entree.paliers.certain > 0 && <PalierBadge palier="certain" />}
                      {entree.paliers.lu > 0 && <PalierBadge palier="lu" />}
                      {entree.paliers.decompose > 0 && <PalierBadge palier="decompose" />}
                      {entree.paliers.partiel > 0 && <PalierBadge palier="partiel" />}
                      <span className="text-muted-foreground">
                        {entree.paliers.certain}·{entree.paliers.lu}·{entree.paliers.decompose}·{entree.paliers.partiel}
                      </span>
                    </span>
                    <BandeauDoublon liens={doublons[entree.code] ?? []} />
                  </button>
                  <ChevronRight className="size-3.5 text-muted-foreground" />
                </div>
              </li>
            ))}
            {file.length === 0 && <li className="p-4 text-xs text-muted-foreground">File vide — rien à valider.</li>}
          </ul>
          <footer className="border-t border-border p-3">
            <label className="flex items-start gap-2 text-[11px] text-muted-foreground">
              <input type="checkbox" checked={acquittement} onChange={(e) => setAcquittement(e.target.checked)} />
              <span>
                J&apos;acquitte explicitement : les seuils ne sont pas calibrés sur des fiches réelles — je prends la
                décision en connaissance de cause (traçée au journal).
              </span>
            </label>
            <button
              type="button"
              onClick={validerEnLot}
              disabled={selection.size === 0 || occupe}
              className="mt-2 flex w-full items-center justify-center gap-2 rounded bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:opacity-40"
              data-testid="valider-lot"
            >
              <Check className="size-3.5" /> Valider la sélection ({selection.size})
            </button>
          </footer>
        </aside>

        {/* fiche + correction */}
        <section className="min-h-0 overflow-auto">
          {!codeActif && <p className="p-6 text-sm text-muted-foreground">Choisissez une fiche dans la file.</p>}
          {codeActif && (
            <>
              <header className="sticky top-0 z-10 flex flex-wrap items-center gap-2 border-b border-border bg-background/95 p-3 backdrop-blur">
                <h2 className="font-mono text-sm font-bold" data-testid="titre-fiche">
                  {codeActif}
                </h2>
                <span className="flex-1" />
                <div className="w-full">
                  <BandeauDoublon liens={doublons[codeActif] ?? []} />
                </div>
                <button
                  type="button"
                  onClick={() => action("valider")}
                  disabled={occupe}
                  className="flex items-center gap-1 rounded bg-emerald-600 px-3 py-1 text-xs font-semibold text-white disabled:opacity-40"
                  data-testid="bouton-valider"
                >
                  <Check className="size-3.5" /> Valider
                </button>
                <button
                  type="button"
                  onClick={() => action("rejeter", { motif: motifRejet })}
                  disabled={occupe || !motifRejet.trim()}
                  title={motifRejet.trim() ? "" : "Motif obligatoire (traçabilité)"}
                  className="flex items-center gap-1 rounded bg-orange-700 px-3 py-1 text-xs font-semibold text-white disabled:opacity-40"
                  data-testid="bouton-rejeter"
                >
                  <CircleX className="size-3.5" /> Rejeter
                </button>
                <input
                  value={motifRejet}
                  onChange={(e) => setMotifRejet(e.target.value)}
                  placeholder="motif du rejet (obligatoire)"
                  className="w-44 rounded border border-border bg-input px-2 py-1 text-xs"
                  data-testid="motif-rejet"
                />
                <button
                  type="button"
                  onClick={() => action("rouvrir", { effacer_corrections: true })}
                  disabled={occupe}
                  title="Rouvrir et effacer les corrections (lève le verrou RG11 explicitement)"
                  className="flex items-center gap-1 rounded border border-border px-2 py-1 text-xs disabled:opacity-40"
                  data-testid="bouton-rouvrir"
                >
                  <Undo2 className="size-3.5" /> Rouvrir
                </button>
              </header>
              <ChampsFiche champs={champs} onCorriger={corriger} onVoirZone={setZone} />
            </>
          )}
        </section>

        {/* visionneuse */}
        <aside className="min-h-0 border-l border-border" data-testid="panneau-pdf">
          <PdfViewer chemin={cheminPdf} zone={zone} />
        </aside>
      </div>
    </div>
  )
}

function ChampsFiche({
  champs,
  onCorriger,
  onVoirZone,
}: {
  champs: ChampExtrait[]
  onCorriger: (champ: ChampExtrait, valeur: string) => void
  onVoirZone: (zone: ZoneASurligner | null) => void
}) {
  const [brouillons, setBrouillons] = useState<Record<string, string>>({})
  return (
    <div className="divide-y divide-border/60">
      {champs.map((champ) => {
        const cle = `${champ.champ}#${champ.rang ?? 0}`
        const brouillon = brouillons[cle] ?? champ.valeur_normalisee ?? ""
        const modifie = brouillon !== (champ.valeur_normalisee ?? "")
        return (
          <div key={cle} className="grid grid-cols-[1fr_1fr_auto] items-center gap-2 px-3 py-1.5 text-xs hover:bg-accent/20">
            <span className="truncate font-mono text-muted-foreground" title={champ.champ}>
              {champ.champ}
              {champ.rang != null ? ` [${champ.rang}]` : ""}
            </span>
            <span className="flex items-center gap-2">
              <input
                value={brouillon}
                onChange={(e) => setBrouillons((b) => ({ ...b, [cle]: e.target.value }))}
                className="w-full rounded border border-transparent bg-input px-1.5 py-0.5 focus:border-primary"
                data-testid={`champ-${champ.champ}`}
              />
              {modifie && (
                <button
                  type="button"
                  onClick={() => onCorriger(champ, brouillon)}
                  className="rounded bg-violet-600 px-1.5 py-0.5 font-semibold text-white"
                  data-testid="bouton-corriger"
                >
                  corriger
                </button>
              )}
            </span>
            <span className="flex items-center gap-1">
              <span className="max-w-40 truncate font-mono text-[10px] text-muted-foreground" title={`valeur brute : ${champ.valeur_brute ?? "—"}`}>
                {champ.valeur_brute ?? "—"}
              </span>
              <PalierBadge palier={palierDeChamp(champ.confiance)} corrige={champ.corrige} />
              {champ.zone && (
                <button
                  type="button"
                  onClick={() =>
                    onVoirZone({ page: champ.zone!.page, x0: champ.zone!.x0, y0: champ.zone!.y0, x1: champ.zone!.x1, y1: champ.zone!.y1 })
                  }
                  title="Voir la zone dans le PDF"
                  className="text-muted-foreground hover:text-foreground"
                  data-testid="bouton-zone"
                >
                  <RotateCcw className="size-3.5" />
                </button>
              )}
            </span>
          </div>
        )
      })}
    </div>
  )
}
