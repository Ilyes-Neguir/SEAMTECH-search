"use client"

// Écran DOSSIERS (lot D) : tableau des fiches (type de gabarit, client,
// bateau, statut) + facettes (compteurs par statut).

import { useCallback, useEffect, useState } from "react"
import { authedFetch } from "@/lib/authed-fetch"
import type { FicheListe } from "@/lib/fiche"
import { cn } from "@/lib/utils"

interface ReponseListe {
  total: number
  page: number
  facettes: Record<string, number>
  fiches: FicheListe[]
}

const STATUT_STYLE: Record<string, string> = {
  a_valider: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  valide: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  rejete: "bg-orange-600/15 text-orange-300 border-orange-600/30",
}

async function jsonFetch<T>(url: string): Promise<T> {
  const res = await authedFetch(url)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(String(body?.detail ?? `HTTP ${res.status}`))
  return body as T
}

export function DossiersApp() {
  const [reponse, setReponse] = useState<ReponseListe | null>(null)
  const [statut, setStatut] = useState<string | "">("")
  const [erreur, setErreur] = useState<string | null>(null)

  const charger = useCallback(async (statutFiltre: string) => {
    try {
      const params = statutFiltre ? `?statut=${encodeURIComponent(statutFiltre)}` : ""
      setReponse(await jsonFetch<ReponseListe>(`/api/fiches${params}`))
    } catch (e) {
      setErreur(e instanceof Error ? e.message : "Liste indisponible.")
    }
  }, [])

  useEffect(() => {
    charger(statut)
  }, [charger, statut])

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-6" data-testid="dossiers-app">
      <h1 className="text-lg font-bold">Dossiers</h1>
      {erreur && <p className="text-sm text-destructive">{erreur}</p>}
      {reponse && (
        <>
          <div className="flex flex-wrap gap-2 text-xs" data-testid="facettes">
            <button
              type="button"
              onClick={() => setStatut("")}
              className={cn("rounded border px-2 py-1", statut === "" ? "border-primary text-primary" : "border-border text-muted-foreground")}
            >
              toutes ({Object.values(reponse.facettes).reduce((a, b) => a + b, 0)})
            </button>
            {Object.entries(reponse.facettes).map(([s, n]) => (
              <button
                key={s}
                type="button"
                onClick={() => setStatut(s)}
                className={cn("rounded border px-2 py-1", statut === s ? "border-primary text-primary" : "border-border text-muted-foreground")}
              >
                {s} ({n})
              </button>
            ))}
          </div>
          <table className="w-full text-left text-xs" data-testid="tableau-fiches">
            <thead className="text-muted-foreground">
              <tr className="border-b border-border">
                <th className="py-2 pr-3">Code</th>
                <th className="py-2 pr-3">Titre</th>
                <th className="py-2 pr-3">Gabarit</th>
                <th className="py-2 pr-3">Client</th>
                <th className="py-2 pr-3">Bateau</th>
                <th className="py-2">Statut</th>
              </tr>
            </thead>
            <tbody>
              {reponse.fiches.map((fiche) => (
                <tr key={fiche.code} className="border-b border-border/50 hover:bg-accent/20">
                  <td className="py-2 pr-3 font-mono font-semibold">
                    <a href={`/dossier/${encodeURIComponent(fiche.code)}`} className="underline-offset-2 hover:underline" data-testid="lien-fiche">
                      {fiche.code}
                    </a>
                  </td>
                  <td className="max-w-48 truncate py-2 pr-3">{fiche.titre}</td>
                  <td className="py-2 pr-3 font-mono text-muted-foreground">{fiche.gabarit}</td>
                  <td className="py-2 pr-3">{fiche.client}</td>
                  <td className="py-2 pr-3">
                    {fiche.bateau}
                    {fiche.bateau_taille ? ` (${fiche.bateau_taille})` : ""}
                  </td>
                  <td className="py-2">
                    <span className={cn("rounded border px-1.5 py-0.5 font-mono text-[10px]", STATUT_STYLE[fiche.statut] ?? "border-border")}>
                      {fiche.statut}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}
