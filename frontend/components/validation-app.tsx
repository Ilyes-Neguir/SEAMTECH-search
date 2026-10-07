"use client"

// Écran VALIDATION (lot D, le cœur métier) — file triée par confiance
// croissante, champs avec valeur brute ET normalisée côte à côte, correction
// inline, surlignage dans le PDF, validation en lot SANS jamais écrire
// « valide » sans décision explicite (RG3). Le verrou de calibration (409)
// s'affiche avec sa sortie et la case d'acquittement explicite.

import { useCallback, useEffect, useRef, useState } from "react"
import { Check, ChevronRight, CircleX, Lock, RefreshCw, RotateCcw, Undo2 } from "lucide-react"
import { PalierBadge } from "@/components/palier-badge"
import { VisionneusePiece, type ZoneASurligner } from "@/components/visionneuse-piece"
import { authedFetch } from "@/lib/authed-fetch"
import { palierDeChamp, type ChampExtrait, type FicheFileEntry, type PieceJointe, type PiecesDeFiche } from "@/lib/fiche"
import { cn } from "@/lib/utils"

// Lot L.2 — « qui a validé quoi » ne vient PLUS d'ici. Le corps de requête
// n'est pas une identité : n'importe qui peut l'écrire. Le proxy serveur
// (/api/fiches/... et /api/validation/lot) ajoute X-SEAMTECH-UTILISATEUR et
// X-SEAMTECH-ROLE depuis le cookie signé, et le backend ne les honore que si le
// jeton de service les accompagne. Le navigateur n'envoie donc plus AUCUN
// « utilisateur » : le champ a été retiré des trois appels ci-dessous pour
// qu'il n'existe qu'une seule source de vérité.

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

// Lot « concurrence » — réponse 409 du backend quand la fiche a changé depuis
// l'ouverture : la correction n'a PAS été appliquée (le travail du collègue est
// préservé). On affiche la valeur à jour et on propose de recharger.
export interface ConflitRevision {
  code?: string
  message?: string
  regle?: string
  revision_envoyee?: number
  revision_actuelle?: number
  valeur_actuelle?: string | null
  corrige_par?: string | null
  corrige_le?: string | null
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
  // Verrou optimiste (migration 021) : la révision de la fiche TELLE QU'ELLE A
  // ÉTÉ LUE. Elle est renvoyée avec chaque correction : si un collègue a
  // enregistré entre-temps, le serveur refuse (409) au lieu d'écraser son
  // travail, et l'opérateur voit la valeur du collègue.
  const [revisionFiche, setRevisionFiche] = useState<number | null>(null)
  const [conflit, setConflit] = useState<ConflitRevision | null>(null)
  const [zone, setZone] = useState<ZoneASurligner | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)
  const [motifRejet, setMotifRejet] = useState("")
  const [acquittement, setAcquittement] = useState(false)
  const [selection, setSelection] = useState<Set<string>>(new Set())
  const [occupe, setOccupe] = useState(false)
  const [aideRaccourcis, setAideRaccourcis] = useState(false)
  const [tempsFiches, setTempsFiches] = useState<number[]>([])
  const [precharge, setPrecharge] = useState<{ code: string; bytes: Uint8Array } | null>(null)
  const debutFiche = useRef<{ code: string; debut: number } | null>(null)
  const motifRef = useRef<HTMLInputElement>(null)

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
    const charger = (essai: number) =>
      jsonFetch<{ par_code: Record<string, LienDoublon[]> }>("/api/validation/doublons", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ codes }),
      })
        .then((corps) => {
          if (!annule) setDoublons(corps.par_code ?? {})
        })
        .catch(() => {
          // Une reprise UNIQUE avant d'abandonner : un échec transitoire de cette
          // requête ferait valider une fiche SANS voir son doublon — exactement ce
          // que le lot existe pour empêcher. (Mesuré en CI : la première requête
          // après le démarrage du front peut échouer.)
          if (essai === 0) {
            setTimeout(() => charger(1), 500)
            return
          }
          console.warn("[doublons] liens indisponibles — le bandeau ne peut pas être affiché.")
          if (!annule) setDoublons({})
        })
    charger(0)
    return () => {
      annule = true
    }
  }, [file])

  useEffect(() => {
    if (!codeActif && file.length > 0) setCodeActif(file[0].code)
  }, [file, codeActif])

  const [piecePdf, setPiecePdf] = useState<PieceJointe | null>(null)
  useEffect(() => {
    if (!codeActif) return
    debutFiche.current = { code: codeActif, debut: Date.now() }
    setMotifRejet("")
    setChamps([])
    setPiecePdf(null)
    setZone(null)
    setConflit(null)
    setRevisionFiche(null)
    jsonFetch<ChampExtrait[]>(`/api/fiches/${encodeURIComponent(codeActif)}/champs`)
      .then(setChamps)
      .catch((e) => setErreur(e instanceof Error ? e.message : "Champs indisponibles."))
    jsonFetch<{ revision?: number }>(`/api/fiches/${encodeURIComponent(codeActif)}`)
      .then((corps) => setRevisionFiche(typeof corps.revision === "number" ? corps.revision : null))
      .catch(() => setRevisionFiche(null)) // sans révision : correction possible, mais non protégée
    jsonFetch<PiecesDeFiche>(`/api/fiches/${encodeURIComponent(codeActif)}/pieces`)
      .then((corps) => setPiecePdf(corps.pieces.find((piece) => piece.is_primary_pdf) ?? corps.pieces.find((piece) => piece.kind === "pdf") ?? null))
      .catch(() => setPiecePdf(null))
  }, [codeActif])

  async function corriger(champ: ChampExtrait, valeur: string) {
    if (!codeActif) return
    setOccupe(true)
    setErreur(null)
    setConflit(null)
    try {
      const corps: { revision?: number } = {}
      if (revisionFiche !== null) corps.revision = revisionFiche
      const reponse = await jsonFetch<{ revision?: number }>(`/api/fiches/${encodeURIComponent(codeActif)}/corriger`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ champ: champ.champ, valeur, rang: champ.rang, ...corps }),
      })
      if (typeof reponse.revision === "number") setRevisionFiche(reponse.revision)
      setMessage(`Champ « ${champ.champ} » corrigé — verrou RG11 armé (une valeur corrigée n'est plus écrasée).`)
      setChamps(await jsonFetch(`/api/fiches/${encodeURIComponent(codeActif)}/champs`))
    } catch (e) {
      const err = e as Error & { status?: number; body?: { detail?: ConflitRevision | string } }
      const detail = err.body?.detail
      if (err.status === 409 && detail && typeof detail === "object") {
        // Concurrence : le collègue a enregistré. On n'écrit RIEN, on montre son
        // travail, et on recharge la fiche pour repartir d'un état juste.
        setConflit(detail)
        setErreur(null)
      } else {
        setErreur(e instanceof Error ? e.message : "Correction refusée.")
      }
    } finally {
      setOccupe(false)
    }
  }

  async function rechargerFiche() {
    if (!codeActif) return
    setConflit(null)
    try {
      setChamps(await jsonFetch<ChampExtrait[]>(`/api/fiches/${encodeURIComponent(codeActif)}/champs`))
      const detail = await jsonFetch<{ revision?: number }>(`/api/fiches/${encodeURIComponent(codeActif)}`)
      if (typeof detail.revision === "number") setRevisionFiche(detail.revision)
      setMessage(`Fiche ${codeActif} rechargée à jour.`)
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "Rechargement impossible.")
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
        body: JSON.stringify({ ...extra }),
      })
      setMessage(`Fiche ${codeActif} : ${actionName} enregistré au journal.`)
      // La décision a fait avancer la révision : l'état local devient périmé.
      setRevisionFiche(null)
      if (debutFiche.current?.code === codeActif) {
        setTempsFiches((mesures) => [...mesures, Math.max(0, Date.now() - debutFiche.current!.debut)])
      }
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
        body: JSON.stringify({ codes: [...selection], acquittement_humain: acquittement }),
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

  async function validerFiche() {
    const entree = file.find((ligne) => ligne.code === codeActif)
    if (entree?.a_anomalies && !window.confirm(`Cette fiche comporte des champs signalés en anomalie. Valider ${codeActif} malgré ces alertes ?`)) return
    await action("valider")
  }

  function naviguer(delta: number) {
    if (file.length === 0) return
    const position = file.findIndex((entree) => entree.code === codeActif)
    const prochaine = Math.min(file.length - 1, Math.max(0, (position < 0 ? 0 : position) + delta))
    setCodeActif(file[prochaine].code)
  }

  // Pré-charge les octets du PDF suivant pendant la fiche courante.
  useEffect(() => {
    const position = file.findIndex((entree) => entree.code === codeActif)
    const suivante = position >= 0 ? file[position + 1] : undefined
    if (!suivante) {
      setPrecharge(null)
      return
    }
    let annule = false
    ;(async () => {
      try {
        const pieces = await jsonFetch<PiecesDeFiche>(`/api/fiches/${encodeURIComponent(suivante.code)}/pieces`)
        const pdf = pieces.pieces.find((piece) => piece.is_primary_pdf) ?? pieces.pieces.find((piece) => piece.kind === "pdf")
        if (!pdf) return
        const reponse = await fetch(`/api/pieces/${encodeURIComponent(pdf.id)}/apercu`, { cache: "no-store" })
        if (!reponse.ok) return
        const bytes = new Uint8Array(await reponse.arrayBuffer())
        if (!annule) setPrecharge({ code: suivante.code, bytes })
      } catch {
        // La précharge optimise le parcours mais ne bloque jamais l'ouverture.
      }
    })()
    return () => { annule = true }
  }, [file, codeActif])

  // Raccourcis clavier hors champs de saisie. Aucun raccourci ne déclenche
  // une correction ou validation implicite sans interaction/décision humaine.
  useEffect(() => {
    const clavier = (event: KeyboardEvent) => {
      const cible = event.target as HTMLElement | null
      const edition = !!cible && (cible.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(cible.tagName))
      if (event.key === "Escape" && aideRaccourcis) {
        event.preventDefault()
        setAideRaccourcis(false)
        return
      }
      if (event.key === "Enter" && cible === motifRef.current) {
        event.preventDefault()
        if (motifRejet.trim() && !occupe) void action("rejeter", { motif: motifRejet })
        return
      }
      if (event.ctrlKey || event.metaKey || event.altKey || edition) return
      const touche = event.key.toLowerCase()
      if (event.key === "?") {
        event.preventDefault()
        setAideRaccourcis((visible) => !visible)
      } else if (touche === "v") {
        event.preventDefault()
        if (!occupe) void validerFiche()
      } else if (touche === "r") {
        event.preventDefault()
        motifRef.current?.focus()
      } else if (touche === "c") {
        event.preventDefault()
        document.querySelector<HTMLInputElement>('[data-testid^="champ-"]')?.focus()
      } else if (touche === "j" || event.key === "ArrowDown") {
        event.preventDefault()
        naviguer(1)
      } else if (touche === "k" || event.key === "ArrowUp") {
        event.preventDefault()
        naviguer(-1)
      }
    }
    window.addEventListener("keydown", clavier)
    return () => window.removeEventListener("keydown", clavier)
  }, [file, codeActif, motifRejet, occupe, champs, aideRaccourcis])

  const tempsTries = [...tempsFiches].sort((a, b) => a - b)
  const medianeMs = tempsTries.length === 0 ? 0 : tempsTries.length % 2
    ? tempsTries[Math.floor(tempsTries.length / 2)]
    : (tempsTries[tempsTries.length / 2 - 1] + tempsTries[tempsTries.length / 2]) / 2

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
      {conflit && (
        /* Concurrence (409) : le collègue a enregistré d'abord. Aucune écriture
           n'a été appliquée — on le dit, on montre SA valeur, et on laisse
           recharger. Aucun « succès » trompeur, aucun travail perdu en silence. */
        <div
          className="flex flex-col gap-1 border-b border-amber-500/40 bg-amber-500/10 px-4 py-2 text-xs text-amber-200"
          data-testid="conflit-revision"
          role="alert"
        >
          <div className="flex items-start gap-2">
            <Lock className="mt-0.5 size-3.5 shrink-0" />
            <span className="flex-1">
              <strong>Conflit de révision — correction NON appliquée.</strong>{" "}
              {conflit.message ??
                "La fiche a été modifiée par un autre poste entre l'ouverture et l'enregistrement."}
            </span>
            <button type="button" onClick={() => setConflit(null)} aria-label="Masquer le conflit">
              <CircleX className="size-3.5" />
            </button>
          </div>
          <dl className="ml-5 grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono text-[11px]">
            <dt>valeur du collègue</dt>
            <dd data-testid="conflit-valeur">{conflit.valeur_actuelle ?? "—"}</dd>
            <dt>corrigée par</dt>
            <dd data-testid="conflit-auteur">{conflit.corrige_par ?? "—"}</dd>
            <dt>le</dt>
            <dd data-testid="conflit-date">{conflit.corrige_le ?? "—"}</dd>
            <dt>révision</dt>
            <dd data-testid="conflit-revisions">
              {conflit.revision_envoyee ?? "?"} → {conflit.revision_actuelle ?? "?"}
            </dd>
          </dl>
          <div className="ml-5">
            <button
              type="button"
              onClick={rechargerFiche}
              disabled={occupe}
              className="flex items-center gap-1 rounded border border-amber-400/50 px-2 py-0.5 font-semibold disabled:opacity-40"
              data-testid="bouton-recharger-fiche"
            >
              <RefreshCw className="size-3" /> Recharger la fiche à jour
            </button>
          </div>
        </div>
      )}
      {aideRaccourcis && (
        <div role="dialog" aria-modal="true" aria-labelledby="aide-raccourcis-titre" className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" data-testid="aide-raccourcis">
          <section className="w-full max-w-md rounded-lg border border-border bg-background p-5 shadow-xl">
            <h2 id="aide-raccourcis-titre" className="text-lg font-semibold">Raccourcis de validation</h2>
            <ul className="mt-3 space-y-2 text-sm">
              <li><kbd>V</kbd> — valider (les anomalies demandent confirmation)</li>
              <li><kbd>R</kbd> — motif de rejet, puis <kbd>Entrée</kbd> pour confirmer</li>
              <li><kbd>C</kbd> — focus sur le premier champ pour correction</li>
              <li><kbd>↓</kbd>/<kbd>J</kbd> — fiche suivante ; <kbd>↑</kbd>/<kbd>K</kbd> — précédente</li>
              <li><kbd>?</kbd> — afficher/masquer cette aide</li>
            </ul>
            <button autoFocus type="button" onClick={() => setAideRaccourcis(false)} className="mt-4 rounded bg-primary px-3 py-1.5 text-sm text-primary-foreground">Fermer</button>
          </section>
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
            {file.length === 0 && tempsFiches.length > 0 && (
            <li className="border-b border-border p-3 text-xs" data-testid="chrono-session">
              Session terminée — {tempsFiches.length} fiche(s) : médiane {(medianeMs / 1000).toFixed(1)} s, min {(tempsTries[0] / 1000).toFixed(1)} s, max {(tempsTries[tempsTries.length - 1] / 1000).toFixed(1)} s. Mesure locale uniquement.
            </li>
          )}
          {file.map((entree) => (
              // data-code : identifiant EXACT de la ligne pour les tests e2e.
              // Sans lui, une ligne se repérait par son texte — or le bandeau de
              // doublon cite l'AUTRE code, donc la ligne du doublon contient aussi
              // le code de la fiche d'origine (mesuré en CI : deux lignes
              // répondaient à la même recherche « 7792-SO »).
              <li key={entree.code} data-code={entree.code}>
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
                    <span className="sr-only" data-testid="confiance-file">{entree.confiance_min ?? "sans confiance"}</span>
                    {entree.a_anomalies && <span className="mt-1 inline-block rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] text-amber-800">Anomalie à vérifier</span>}
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
            <button type="button" onClick={() => setAideRaccourcis(true)} className="mb-2 text-[11px] text-muted-foreground underline" data-testid="bouton-aide-raccourcis">? Raccourcis clavier</button>
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
                  onClick={() => void validerFiche()}
                  disabled={occupe}
                  className="flex items-center gap-1 rounded bg-emerald-600 px-3 py-1 text-xs font-semibold text-white disabled:opacity-40"
                  data-testid="bouton-valider"
                  title="Raccourci : V"
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
                  ref={motifRef}
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
          <VisionneusePiece piece={piecePdf} zone={zone} donneesPrechargees={precharge?.code === codeActif ? precharge.bytes : null} />
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
                onFocus={() => onVoirZone(champ.zone ? { page: champ.zone.page, x0: champ.zone.x0, y0: champ.zone.y0, x1: champ.zone.x1, y1: champ.zone.y1 } : null)}
                className="w-full rounded border border-transparent bg-input px-1.5 py-0.5 focus:border-primary"
                data-testid={`champ-${champ.champ}`}
                data-zone={champ.zone ? "true" : "false"}
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
