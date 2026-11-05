/**
 * Logique pure de la comparaison d'un GROUPE de fiches.
 *
 * Elle vit ici, hors du composant, pour une raison simple : c'est elle qui
 * décide de ce que l'écran MONTRE, et une décision d'affichage fausse est
 * pire qu'un écran laid. « Masquer tout ce qui est identique » paraît anodin ;
 * c'est pourtant l'endroit où un écart de 0 % peut être confondu avec une
 * valeur absente, et où l'on finirait par cacher précisément ce qu'il fallait
 * voir. Ces fonctions sont donc testées hors navigateur, dans
 * `comparaison-groupe.test.ts`.
 */

export const MAX_FICHES_GROUPE = 12

export interface ResumeExtractionGroupe {
  id_extraction: number
  code_fiche: string | null
  code_fiche_metier?: string | null
  type_voile: string | null
  modele_bateau_brut: string | null
  gamme: string | null
  modele_lie?: string | { nom_canonique: string } | null
}

export interface LigneComparaison {
  champ: string
  libelle: string
  numerique: boolean
  unite: string | null
  valeurs: (number | string | null)[]
  etats: string[]
  nb_fiches_avec_valeur: number
  identique: boolean | null
  deltas?: (number | null)[]
  deltas_cm?: (number | null)[]
  deltas_pct?: (number | null)[]
  min?: number | null
  max?: number | null
  etendue?: number | null
  ecart_max_pct?: number | null
}

export interface ComparaisonGroupeReponse {
  nb_fiches: number
  reference: { rang: number; id_extraction: number | null; code_fiche: string | null; libelle: string }
  colonnes: ResumeExtractionGroupe[]
  comparable: boolean
  avertissements: string[]
  blocs: {
    identification: LigneComparaison[]
    dimensions: LigneComparaison[]
    materiaux: LigneComparaison[]
    qualite: LigneComparaison[]
  }
  synthese: {
    dimensions_comparables: number
    ecart_max_pct: number | null
    ecart_max_champ: string | null
    ecart_max_libelle: string | null
    effectif_max: number
  }
}

/** Un nombre, ou « — » quand il n'existe pas. Jamais un zéro inventé. */
export function formaterNombre(nombre: number | null | undefined, decimales = 2): string {
  if (nombre === null || nombre === undefined || Number.isNaN(nombre)) return "—"
  return nombre.toLocaleString("fr-FR", {
    minimumFractionDigits: decimales,
    maximumFractionDigits: decimales,
  })
}

/** Un écart, avec son signe : « +2,00 » se lit plus vite que « 2,00 ». */
export function formaterSigne(nombre: number | null | undefined, decimales = 1): string {
  if (nombre === null || nombre === undefined || Number.isNaN(nombre)) return "—"
  return `${nombre > 0 ? "+" : ""}${formaterNombre(nombre, decimales)}`
}

/** Trois nuances d'affichage, et rien de plus : c'est une aide, pas un verdict. */
export function classeEcart(pourcent: number | null | undefined): string {
  if (pourcent === null || pourcent === undefined) return "bg-muted text-muted-foreground"
  const absolu = Math.abs(pourcent)
  if (absolu <= 1) return "bg-emerald-500/20 text-emerald-800 dark:text-emerald-200"
  if (absolu <= 5) return "badge-attention"
  return "bg-red-500/20 text-red-800 dark:text-red-200"
}

/**
 * La ligne porte-t-elle un ÉCART (quelque chose qui diffère) ?
 *
 * C'est la fonction du bouton « Écarts seulement ». Deux pièges, chacun
 * verrouillé par un test :
 *
 *   - une ligne dont AUCUNE valeur n'a été lue ne porte pas d'écart, et il ne
 *     faut surtout pas la compter comme « identique » : ce n'est pas une
 *     égalité constatée, c'est une absence ;
 *   - une ligne de TEXTE n'a pas d'écart numérique : elle ne diffère que si
 *     ses valeurs sont reconnues différentes, et ne prétend jamais différer
 *     dans le doute (« identique » à `null`).
 */
export function ligneADesEcart(ligne: LigneComparaison): boolean {
  if (ligne.numerique) {
    return ligne.ecart_max_pct !== null && ligne.ecart_max_pct !== undefined && ligne.ecart_max_pct !== 0
  }
  return ligne.identique === false
}

/**
 * La ligne est-elle MESURÉE — c'est-à-dire comparée pour de vrai ?
 *
 * La distinction avec `ligneADesEcart` est tout l'enjeu du bouton : masquer
 * une ligne mesurée à 0 % d'écart est légitime (elle n'apprend rien), mais la
 * faire disparaître SANS LE DIRE ferait croire qu'on n'a rien comparé. L'écran
 * affiche donc « N attribut(s) identique(s) masqué(s) » : le silence est
 * interdit, l'égalité constatée est annoncée.
 */
export function ligneEstMesuree(ligne: LigneComparaison): boolean {
  if (ligne.numerique) return ligne.nb_fiches_avec_valeur >= 2
  return ligne.identique !== null
}

/** Nom d'affichage d'une colonne : son code métier, à défaut son identifiant. */
export function nomFiche(resume: ResumeExtractionGroupe): string {
  return resume.code_fiche ?? `#${resume.id_extraction}`
}

/** Modèle de bateau affichable : le nom canonique résolu, à défaut le brut lu. */
export function nomModele(resume: ResumeExtractionGroupe): string {
  if (typeof resume.modele_lie === "string" && resume.modele_lie) return resume.modele_lie
  if (resume.modele_lie && typeof resume.modele_lie === "object") return resume.modele_lie.nom_canonique
  return resume.modele_bateau_brut ?? "bateau non lu"
}
