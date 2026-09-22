"use client"

// Écran NOUVEAU DOSSIER (lot D) : dépôt d'un dossier complet (porte A, lot C)
// et suivi du lot (progression, raisons d'échec, fichiers restants — GET
// /lots/{id}). Un refus est un RÉSULTAT affiché avec sa raison, jamais un
// crash silencieux.

import { useCallback, useEffect, useState } from "react"
import { FolderInput, RefreshCw } from "lucide-react"
import { authedFetch } from "@/lib/authed-fetch"
import type { LotDetail } from "@/lib/fiche"
import { cn } from "@/lib/utils"

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await authedFetch(url, init)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw Object.assign(new Error(String(body?.detail ?? `HTTP ${res.status}`)), { status: res.status })
  return body as T
}

export function NouveauApp() {
  const [dossier, setDossier] = useState("")
  const [resultat, setResultat] = useState<{ statut: string; fiche: string | null; raison: string | null; pieces: number } | null>(null)
  const [idLot, setIdLot] = useState<number | null>(null)
  const [lot, setLot] = useState<LotDetail | null>(null)
  const [occupe, setOccupe] = useState(false)
  const [erreur, setErreur] = useState<string | null>(null)

  const chargerLot = useCallback(async (id: number) => {
    try {
      const detail = await jsonFetch<LotDetail>(`/api/lots/${id}`)
      setLot(detail)
      if (detail.statut === "en_cours") setTimeout(() => chargerLot(id), 1000)
    } catch {
      setLot(null)
    }
  }, [])

  useEffect(() => {
    if (idLot != null) chargerLot(idLot)
  }, [idLot, chargerLot])

  async function deposer() {
    setOccupe(true)
    setErreur(null)
    setResultat(null)
    try {
      const corps = await jsonFetch<{ statut: string; fiche: string | null; raison: string | null; pieces: number; id_lot: number | null; lot?: { id_lot: number } }>(
        "/api/imports/dossier",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ dossier }),
        },
      )
      setResultat({ statut: corps.statut, fiche: corps.fiche, raison: corps.raison, pieces: corps.pieces })
      const lotId = corps.id_lot ?? corps.lot?.id_lot
      if (lotId) setIdLot(lotId)
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "Dépôt impossible.")
    } finally {
      setOccupe(false)
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6" data-testid="nouveau-app">
      <div>
        <h1 className="flex items-center gap-2 text-lg font-bold">
          <FolderInput className="size-5" /> Déposer un dossier complet
        </h1>
        <p className="mt-1 text-xs text-muted-foreground">
          Le dossier de fabrication (fiche PDF + croquis, plans, photos). La fiche est reconnue et extraite ; les autres
          fichiers sont rattachés comme pièces jointes (non analysés, RG12). La fiche arrive en <em>a_valider</em> —
          jamais valide sans décision humaine (RG3). Idempotent : rejouer un dossier ne crée rien.
        </p>
      </div>
      <div className="flex gap-2">
        <input
          value={dossier}
          onChange={(e) => setDossier(e.target.value)}
          placeholder="/archive/CLIENT-7792-SO"
          className="flex-1 rounded border border-border bg-input px-3 py-2 font-mono text-sm"
          data-testid="champ-dossier"
        />
        <button
          type="button"
          onClick={deposer}
          disabled={!dossier.trim() || occupe}
          className="rounded bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-40"
          data-testid="bouton-deposer"
        >
          Déposer
        </button>
      </div>
      {erreur && <p className="text-sm text-destructive" data-testid="erreur-depot">{erreur}</p>}
      {resultat && (
        <div
          className={cn(
            "rounded border p-4 text-sm",
            resultat.statut === "traite" ? "border-emerald-500/30 bg-emerald-500/10" : "border-amber-500/30 bg-amber-500/10",
          )}
          data-testid="resultat-depot"
        >
          <p className="font-semibold">
            {resultat.statut === "traite" ? "Dossier traité" : `Dossier ${resultat.statut}`}{" "}
            {resultat.fiche && <>— fiche {resultat.fiche}</>} {resultat.pieces > 0 && <>— {resultat.pieces} pièce(s) jointe(s)</>}
          </p>
          {resultat.raison && <p className="mt-1 text-xs text-muted-foreground">Raison : {resultat.raison}</p>}
          {resultat.fiche && (
            <a href={`/dossier/${encodeURIComponent(resultat.fiche)}`} className="mt-2 inline-block text-xs text-primary underline">
              Ouvrir la fiche →
            </a>
          )}
        </div>
      )}
      {lot && (
        <div className="rounded border border-border p-4" data-testid="suivi-lot">
          <h2 className="flex items-center justify-between text-sm font-semibold">
            <span>
              Lot #{lot.id_lot} — {lot.statut}
            </span>
            <button type="button" onClick={() => chargerLot(lot.id_lot)} aria-label="Rafraîchir le lot" className="text-muted-foreground hover:text-foreground">
              <RefreshCw className="size-4" />
            </button>
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            {lot.nb_traites + lot.nb_echecs} / {lot.nb_dossiers} dossier(s) — {lot.progression_pct ?? 0} %
          </p>
          <ul className="mt-2 space-y-1 text-xs">
            {lot.dossiers.map((ligne) => (
              <li key={ligne.chemin_dossier} className="flex items-center justify-between gap-2" data-testid="ligne-lot">
                <span className="truncate font-mono">{ligne.chemin_dossier}</span>
                <span className={ligne.statut === "traite" ? "text-emerald-300" : "text-orange-300"}>
                  {ligne.statut}
                  {ligne.raison ? ` — ${ligne.raison}` : ""}
                </span>
              </li>
            ))}
            {lot.restants.length > 0 && <li className="text-muted-foreground">{lot.restants.length} fichier(s) restant(s)</li>}
          </ul>
        </div>
      )}
    </div>
  )
}
