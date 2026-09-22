"use client"

import { useEffect, useState } from "react"

interface Tableau {
  taux_extraction_auto: {
    definition: string
    unite: string
    periode: string
    total_champs: number
    auto: number
    corriges: number
    taux_auto: number | null
    taux_auto_pct: number | null
  }
  taux_correction_par_champ: Array<{
    champ: string
    total: number
    corriges: number
    auto: number
    taux_correction: number
    taux_correction_pct: number
  }>
  temps_validation: {
    definition: string
    unite: string
    periode: string
    nb_fiches_validees: number
    mediane_s: number | null
    p95_s: number | null
    moyenne_s: number | null
    min_s: number | null
    max_s: number | null
    mediane_min: number | null
    p95_min: number | null
  }
  anomalies_frequentes: Array<{ code: string; gravite: string; nb: number; a_traiter: number }>
  volume_par_statut: {
    definition: string
    unite: string
    periode: string
    total: number
    en_attente_validation: number
    par_statut: Record<string, number>
  }
  usage_recherches: {
    definition: string
    unite: string
    periode: string
    total_30j: number
    sans_resultat_30j: number
    part_sans_resultat_30j: number
    part_sans_resultat_pct_30j: number
    par_jour: Array<{ jour: string; total: number; sans_resultat: number; part_sans_resultat_pct: number }>
    par_canal: Array<{ canal: string; total: number; sans_resultat: number; part_sans_resultat_pct: number }>
  }
  lots: {
    definition: string
    unite: string
    periode: string
    total_lots: number
    total_dossiers: number
    par_statut_lot: Record<string, number>
    par_statut_dossier: Record<string, number>
  }
}

export function QualiteApp() {
  const [data, setData] = useState<Tableau | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)
  const [chargement, setChargement] = useState(true)

  useEffect(() => {
    fetch("/api/qualite/tableau-de-bord")
      .then(async (r) => {
        if (!r.ok) throw new Error(await r.text())
        return r.json()
      })
      .then(setData)
      .catch((e) => setErreur(e instanceof Error ? e.message : String(e)))
      .finally(() => setChargement(false))
  }, [])

  if (chargement) return <div className="p-6 text-sm text-muted-foreground">Chargement tableau qualité…</div>
  if (erreur) return <div className="p-6 text-sm text-destructive">Erreur: {erreur}</div>
  if (!data) return null

  return (
    <div className="space-y-6 p-6" data-testid="qualite-app">
      <h1 className="text-xl font-bold">Tableau de bord qualité — données réelles</h1>
      <p className="text-xs text-muted-foreground">
        Sources : fiche_champ_extrait, fiche_validation, fiche_anomalie, recherche_log, lot_import/dossier. Aucune donnée inventée.
      </p>

      <section className="rounded border p-4">
        <h2 className="font-semibold">Taux extraction auto</h2>
        <p className="text-xs text-muted-foreground">{data.taux_extraction_auto.definition} — {data.taux_extraction_auto.unite} — {data.taux_extraction_auto.periode}</p>
        <p className="mt-2 text-2xl font-mono">{data.taux_extraction_auto.taux_auto_pct ?? "—"} %</p>
        <p className="text-xs">{data.taux_extraction_auto.auto} auto / {data.taux_extraction_auto.total_champs} total ({data.taux_extraction_auto.corriges} corrigés)</p>
      </section>

      <section className="rounded border p-4">
        <h2 className="font-semibold">Taux correction par champ (classement → quel gabarit améliorer)</h2>
        <p className="text-xs text-muted-foreground">Définition: par champ nb corrigés/total — Unité: ratio — Période: tout historique</p>
        <table className="mt-2 w-full text-xs">
          <thead>
            <tr className="text-left text-muted-foreground">
              <th>Champ</th>
              <th>Total</th>
              <th>Corrigés</th>
              <th>Taux</th>
            </tr>
          </thead>
          <tbody>
            {data.taux_correction_par_champ.slice(0, 15).map((r) => (
              <tr key={r.champ} className="border-t">
                <td className="py-1 font-mono">{r.champ}</td>
                <td>{r.total}</td>
                <td>{r.corriges}</td>
                <td>{r.taux_correction_pct} %</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="rounded border p-4">
        <h2 className="font-semibold">Temps validation par fiche médiane+p95</h2>
        <p className="text-xs text-muted-foreground">{data.temps_validation.definition} — {data.temps_validation.unite} — {data.temps_validation.periode}</p>
        <p className="mt-2">
          n={data.temps_validation.nb_fiches_validees} — médiane: {data.temps_validation.mediane_s?.toFixed(1) ?? "—"} s ({data.temps_validation.mediane_min ?? "—"} min) —
          p95: {data.temps_validation.p95_s?.toFixed(1) ?? "—"} s ({data.temps_validation.p95_min ?? "—"} min)
        </p>
      </section>

      <section className="rounded border p-4">
        <h2 className="font-semibold">Anomalies plus fréquentes par type</h2>
        <p className="text-xs text-muted-foreground">Définition: COUNT par code — Unité: compte — Période: tout historique</p>
        <ul className="mt-2 text-xs">
          {data.anomalies_frequentes.slice(0, 15).map((a) => (
            <li key={`${a.code}-${a.gravite}`}>
              {a.code} ({a.gravite}): {a.nb} dont {a.a_traiter} à traiter
            </li>
          ))}
        </ul>
      </section>

      <section className="rounded border p-4">
        <h2 className="font-semibold">Volume fiches par statut / en attente validation</h2>
        <p className="text-xs text-muted-foreground">{data.volume_par_statut.definition} — {data.volume_par_statut.unite} — {data.volume_par_statut.periode}</p>
        <p className="mt-2">Total: {data.volume_par_statut.total} — En attente: {data.volume_par_statut.en_attente_validation}</p>
        <p className="text-xs font-mono">{JSON.stringify(data.volume_par_statut.par_statut)}</p>
      </section>

      <section className="rounded border p-4">
        <h2 className="font-semibold">Usage recherches / jour + part sans résultat (split canal)</h2>
        <p className="text-xs text-muted-foreground">{data.usage_recherches.definition} — {data.usage_recherches.unite} — {data.usage_recherches.periode}</p>
        <p className="mt-2">30j total: {data.usage_recherches.total_30j} — sans résultat: {data.usage_recherches.sans_resultat_30j} ({data.usage_recherches.part_sans_resultat_pct_30j}%)</p>
        <div className="mt-2">
          <p className="text-xs font-semibold">Par canal (filtres-&gt;&apos;canal&apos;) — combien utilisateur vs assistant</p>
          <ul className="text-xs">
            {data.usage_recherches.par_canal.map((c) => (
              <li key={c.canal}>
                {c.canal}: {c.total} total, {c.sans_resultat} sans résultat ({c.part_sans_resultat_pct}%)
              </li>
            ))}
          </ul>
        </div>
        <details className="mt-2">
          <summary className="cursor-pointer text-xs">Détail par jour (30j)</summary>
          <ul className="text-xs">
            {data.usage_recherches.par_jour.slice(0, 30).map((j) => (
              <li key={j.jour}>
                {j.jour}: {j.total} ({j.sans_resultat} sans résultat, {j.part_sans_resultat_pct}%)
              </li>
            ))}
          </ul>
        </details>
      </section>

      <section className="rounded border p-4">
        <h2 className="font-semibold">Lots / imports</h2>
        <p className="text-xs text-muted-foreground">{data.lots.definition} — {data.lots.unite} — {data.lots.periode}</p>
        <p className="mt-2">Lots: {data.lots.total_lots} — Dossiers: {data.lots.total_dossiers}</p>
        <p className="text-xs">Par statut lot: {JSON.stringify(data.lots.par_statut_lot)}</p>
        <p className="text-xs">Par statut dossier: {JSON.stringify(data.lots.par_statut_dossier)}</p>
      </section>
    </div>
  )
}
