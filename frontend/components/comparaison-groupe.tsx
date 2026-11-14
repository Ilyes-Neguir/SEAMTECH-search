"use client"

// Écran COMPARAISON — vue GROUPE (attributs à gauche, une colonne par fiche).
//
// L'atelier ne compare pas deux voiles, il compare un groupe : trois voiles du
// même bateau, quatre du même type. La vue « deux voiles » reste disponible ;
// celle-ci prend le relais dès qu'il y en a plus.
//
// Trois règles d'affichage, les mêmes que côté backend :
//
//   - un écart est une soustraction, affichée dès deux fiches. Aucun seuil,
//     aucun effectif minimum : ce n'est pas une estimation ;
//   - une valeur non lue affiche « — » et ne produit aucun écart. Un tiret
//     n'est jamais un zéro ;
//   - la référence est NOMMÉE et visible : un pourcentage sans cible ne veut
//     rien dire. Un badge « réf. » marque la colonne de référence, et la
//     liste déroulante permet d'en changer.
//
// Le bouton « Écarts seulement » masque les attributs identiques sur tout le
// groupe — c'est ce qui rend une table de huit colonnes lisible.

import { Fragment, useEffect, useMemo, useRef, useState } from "react"
import { AlertTriangle, CheckCircle2 } from "lucide-react"
import { authedFetch } from "@/lib/authed-fetch"
import { cn } from "@/lib/utils"
import {
  MAX_FICHES_GROUPE,
  classeEcart,
  formaterNombre,
  formaterSigne,
  ligneADesEcart,
  ligneEstMesuree,
  nomFiche,
  nomModele,
  type ComparaisonGroupeReponse,
  type LigneComparaison,
  type ResumeExtractionGroupe as ResumeExtraction,
} from "@/lib/comparaison-groupe"

async function jsonFetch<T>(url: string): Promise<T> {
  const res = await authedFetch(url)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = (body as { detail?: unknown })?.detail
    throw new Error(typeof detail === "string" ? detail : `HTTP ${res.status}`)
  }
  return body as T
}

const BLOCS: { cle: keyof ComparaisonGroupeReponse["blocs"]; titre: string }[] = [
  { cle: "identification", titre: "Identification" },
  { cle: "dimensions", titre: "Dimensions (cotes)" },
  { cle: "materiaux", titre: "Matériaux" },
  { cle: "qualite", titre: "Qualité & traçabilité" },
]

export function ComparaisonGroupe({ extractions }: { extractions: ResumeExtraction[] }) {
  const [selection, setSelection] = useState<number[]>([])
  const [reference, setReference] = useState<number | null>(null)
  const [resultat, setResultat] = useState<ComparaisonGroupeReponse | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)
  const [ecartsSeulement, setEcartsSeulement] = useState(false)
  const [recherche, setRecherche] = useState("")
  const generation = useRef(0)

  // La sélection peut contenir un identifiant qui n'est plus dans la liste
  // (extraction supprimée entre deux chargements) : on ne compare que ce qui
  // existe encore, plutôt que d'échouer sur un fantôme.
  const idsActifs = useMemo(
    () => selection.filter((id) => extractions.some((e) => e.id_extraction === id)),
    [selection, extractions],
  )
  const cleSelection = idsActifs.join(",")
  const referenceEffective = reference !== null && idsActifs.includes(reference) ? reference : (idsActifs[0] ?? null)

  useEffect(() => {
    if (idsActifs.length < 2) {
      setResultat(null)
      return
    }
    const jeton = ++generation.current
    const url = `/api/comparaison-voiles/plusieurs?ids=${encodeURIComponent(cleSelection)}${
      referenceEffective !== null ? `&reference=${referenceEffective}` : ""
    }`
    jsonFetch<ComparaisonGroupeReponse>(url)
      .then((corps) => {
        if (jeton === generation.current) {
          setResultat(corps)
          setErreur(null)
        }
      })
      .catch((e: unknown) => {
        if (jeton === generation.current) {
          setResultat(null)
          setErreur(e instanceof Error ? e.message : "Comparaison refusée.")
        }
      })
  }, [cleSelection, referenceEffective])

  function basculer(id: number) {
    setSelection((precedente) => {
      if (precedente.includes(id)) return precedente.filter((x) => x !== id)
      if (precedente.length >= MAX_FICHES_GROUPE) return precedente
      return [...precedente, id]
    })
  }

  const liste = extractions.filter((e) => {
    if (!recherche.trim()) return true
    const texte = `${nomFiche(e)} ${e.type_voile ?? ""} ${nomModele(e)} ${e.gamme ?? ""}`.toLowerCase()
    return texte.includes(recherche.trim().toLowerCase())
  })

  /** Masquer ce qui ne diffère pas, mais ne jamais le faire en silence. */
  function ligneVisible(ligne: LigneComparaison): boolean {
    if (!ecartsSeulement) return true
    return ligneADesEcart(ligne)
  }

  const toutesLesLignes = resultat
    ? BLOCS.flatMap((bloc) => resultat.blocs[bloc.cle])
    : []
  const identiquesMasques = ecartsSeulement
    ? toutesLesLignes.filter((ligne) => !ligneADesEcart(ligne) && ligneEstMesuree(ligne)).length
    : 0

  return (
    <div className="space-y-3" data-testid="comparaison-groupe">
      <section className="rounded border border-border p-3" data-testid="selection-groupe">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-xs font-semibold">
            Fiches à comparer — {idsActifs.length} sélectionnée(s), {MAX_FICHES_GROUPE} au plus
          </h3>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setEcartsSeulement((v) => !v)}
              className="rounded border border-border px-2 py-1 text-xs hover:bg-accent/50"
              data-testid="bascule-ecarts"
              aria-pressed={ecartsSeulement}
            >
              {ecartsSeulement ? "Tout afficher" : "Écarts seulement"}
            </button>
            <button
              type="button"
              onClick={() => {
                setSelection([])
                setReference(null)
              }}
              className="rounded border border-border px-2 py-1 text-xs hover:bg-accent/50"
              data-testid="vider-selection"
            >
              Vider
            </button>
          </div>
        </div>

        <input
          value={recherche}
          onChange={(e) => setRecherche(e.target.value)}
          placeholder="Filtrer (code, type, bateau, gamme)…"
          className="mb-2 w-full rounded border border-border bg-input px-2 py-1 text-xs text-foreground"
          data-testid="recherche-groupe"
          aria-label="Filtrer les fiches à comparer"
        />

        <ul className="max-h-56 space-y-0.5 overflow-y-auto" data-testid="liste-groupe">
          {liste.map((extraction) => (
            <li key={extraction.id_extraction}>
              <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs hover:bg-accent/40">
                <input
                  type="checkbox"
                  checked={selection.includes(extraction.id_extraction)}
                  onChange={() => basculer(extraction.id_extraction)}
                  data-testid={`case-groupe-${extraction.id_extraction}`}
                />
                <span className="font-mono font-semibold">{nomFiche(extraction)}</span>
                <span className="truncate text-muted-foreground">
                  {extraction.type_voile ?? "type non lu"} · {nomModele(extraction)}
                </span>
              </label>
            </li>
          ))}
        </ul>

        {idsActifs.length > 1 && (
          <div className="mt-2 flex items-center gap-2 border-t border-border pt-2">
            <label htmlFor="reference-groupe" className="text-xs text-muted-foreground">
              Référence des écarts
            </label>
            <select
              id="reference-groupe"
              value={referenceEffective ?? ""}
              onChange={(e) => setReference(Number(e.target.value))}
              className="rounded border border-border bg-input px-2 py-1 text-xs text-foreground"
              data-testid="select-reference"
            >
              {idsActifs.map((id) => {
                const fiche = extractions.find((e) => e.id_extraction === id)
                return (
                  <option key={id} value={id}>
                    {fiche ? nomFiche(fiche) : `#${id}`}
                  </option>
                )
              })}
            </select>
            <span className="text-[11px] text-muted-foreground">
              Les écarts sont calculés par rapport à cette fiche — un pourcentage sans cible ne veut rien dire.
            </span>
          </div>
        )}

        {idsActifs.length < 2 && (
          <p className="mt-2 text-xs text-muted-foreground" data-testid="invite-selection">
            Sélectionnez au moins deux fiches.
          </p>
        )}
      </section>

      {erreur && (
        <div
          className="rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
          role="alert"
          data-testid="erreur-groupe"
        >
          {erreur}
        </div>
      )}

      {resultat && (
        <section className="space-y-2" data-testid="resultat-groupe">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={cn(
                "flex items-center gap-1 rounded px-2 py-1 text-xs font-semibold",
                resultat.comparable
                  ? "bg-emerald-500/20 text-emerald-800 dark:text-emerald-200"
                  : "badge-attention",
              )}
              data-testid="badge-comparable-groupe"
              data-comparable={resultat.comparable ? "oui" : "non"}
            >
              {resultat.comparable ? <CheckCircle2 className="size-3.5" /> : <AlertTriangle className="size-3.5" />}
              {resultat.comparable
                ? "Même modèle de bateau, même type de voile"
                : "Comparaison indicative (pas même modèle + type)"}
            </span>
            <span className="text-xs text-muted-foreground" data-testid="synthese-groupe">
              {resultat.synthese.dimensions_comparables} cote(s) comparable(s)
              {resultat.synthese.ecart_max_pct !== null
                ? ` · écart max ${formaterSigne(resultat.synthese.ecart_max_pct)} % (${resultat.synthese.ecart_max_libelle ?? resultat.synthese.ecart_max_champ})`
                : ""}
              {" · "}
              plus grand effectif : {resultat.synthese.effectif_max} fiche(s) sur une même cote
            </span>
          </div>

          {resultat.avertissements.length > 0 && (
            <ul className="space-y-1" data-testid="avertissements-groupe">
              {resultat.avertissements.map((texte, i) => (
                <li key={i} className="flex items-start gap-1 rounded bg-amber-500/10 px-2 py-1 text-[11px]">
                  <AlertTriangle className="mt-0.5 size-3 shrink-0" aria-hidden /> {texte}
                </li>
              ))}
            </ul>
          )}

          {ecartsSeulement && (
            <p className="text-[11px] text-muted-foreground" data-testid="identiques-masques">
              {identiquesMasques > 0
                ? `${identiquesMasques} attribut(s) identique(s) sur tout le groupe masqué(s) : ils ont bien été comparés, ils ne diffèrent pas.`
                : "Aucun attribut identique masqué : ce qui ne s’affiche pas n’a pas pu être comparé (valeur non lue)."}
            </p>
          )}

          <div className="overflow-x-auto rounded border border-border">
            <table className="w-full text-xs" data-testid="tableau-groupe">
              <caption className="sr-only">
                Attributs comparés en lignes, une colonne par fiche. Les écarts sont calculés par rapport à la fiche
                de référence.
              </caption>
              <thead>
                <tr className="border-b border-border bg-muted/50 text-left">
                  <th scope="col" className="px-2 py-1.5">
                    Attribut
                  </th>
                  {resultat.colonnes.map((colonne, rang) => (
                    <th key={colonne.id_extraction} scope="col" className="px-2 py-1.5">
                      {colonne.code_fiche_metier ? (
                        <a
                          href={`/fiches/${encodeURIComponent(colonne.code_fiche_metier)}`}
                          className="font-mono font-semibold text-primary underline-offset-2 hover:underline"
                        >
                          {nomFiche(colonne)}
                        </a>
                      ) : (
                        <span className="font-mono font-semibold">{nomFiche(colonne)}</span>
                      )}
                      {rang === resultat.reference.rang && (
                        <span className="ml-1 rounded bg-primary/20 px-1 py-0.5 text-[10px] font-semibold">
                          réf.
                        </span>
                      )}
                    </th>
                  ))}
                  <th scope="col" className="px-2 py-1.5">
                    Écart max
                  </th>
                </tr>
              </thead>
              <tbody>
                {BLOCS.map((bloc) => {
                  const lignes = resultat.blocs[bloc.cle].filter(ligneVisible)
                  if (lignes.length === 0) return null
                  return (
                    <Fragment key={bloc.cle}>
                      <tr className="bg-muted/30">
                        <th
                          scope="colgroup"
                          colSpan={resultat.colonnes.length + 2}
                          className="px-2 py-1 text-left text-[11px] font-semibold uppercase tracking-wide"
                        >
                          {bloc.titre}
                        </th>
                      </tr>
                      {lignes.map((ligne) => (
                        <tr
                          key={`${bloc.cle}-${ligne.champ}`}
                          className="border-b border-border/40"
                          data-testid="ligne-groupe"
                          data-champ={ligne.champ}
                        >
                          <th scope="row" className="px-2 py-1.5 text-left font-medium">
                            {ligne.libelle}
                            {ligne.unite ? (
                              <span className="ml-1 font-normal text-muted-foreground">({ligne.unite})</span>
                            ) : null}
                            {ligne.identique === false && (
                              <span className="ml-1 font-semibold text-warning" title="Valeurs différentes">
                                ≠
                              </span>
                            )}
                          </th>
                          {ligne.valeurs.map((valeur, rang) => (
                            <td key={rang} className="px-2 py-1.5 tabular-nums">
                              {valeur === null || valeur === undefined ? (
                                <span className="text-muted-foreground" title="Valeur non lue">
                                  —
                                </span>
                              ) : (
                                <>
                                  <span>{typeof valeur === "number" ? formaterNombre(valeur) : String(valeur)}</span>
                                  {ligne.deltas_cm?.[rang] != null && (
                                    <span className="block text-[10px] text-muted-foreground">
                                      {formaterSigne(ligne.deltas_cm[rang])} cm
                                    </span>
                                  )}
                                  {ligne.deltas_pct?.[rang] != null && (
                                    <span
                                      className={cn(
                                        "ml-1 rounded px-1 py-0.5 text-[10px] tabular-nums",
                                        classeEcart(ligne.deltas_pct[rang]),
                                      )}
                                      data-testid={`ecart-${ligne.champ}-${rang}`}
                                    >
                                      {formaterSigne(ligne.deltas_pct[rang])} %
                                    </span>
                                  )}
                                </>
                              )}
                            </td>
                          ))}
                          <td className="px-2 py-1.5 tabular-nums">
                            {ligne.numerique && ligne.ecart_max_pct != null ? (
                              <span className={cn("rounded px-1.5 py-0.5", classeEcart(ligne.ecart_max_pct))}>
                                {formaterSigne(ligne.ecart_max_pct)} %
                              </span>
                            ) : (
                              <span className="text-muted-foreground">—</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-muted-foreground">
            Couleurs des écarts (affichage seulement) : ≤ 1 % vert, ≤ 5 % ambre, au-delà rouge. Un « — » signifie
            valeur non lue : jamais un écart inventé. « réf. » marque la colonne à partir de laquelle les écarts sont
            calculés.
          </p>
        </section>
      )}
    </div>
  )
}
