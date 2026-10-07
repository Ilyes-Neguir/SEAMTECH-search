// Lot D — contrat des endpoints fiches / validation / lots (§17.5).
// Mirroir TypeScript des routes FastAPI du backend (seamtech_search/fiches/routes.py).

export interface Zone {
  x0: number
  x1: number
  y0: number
  y1: number
  page: number // 0-based
}

export interface ChampExtrait {
  champ: string
  rang: number | null
  valeur_brute: string | null
  valeur_normalisee: string | null
  methode: string | null
  confiance: number | null
  page: number | null
  zone: Zone | null
  corrige: boolean
  corrige_par: string | null
}

export interface Paliers {
  certain: number
  lu: number
  decompose: number
  partiel: number
}

export interface FicheFileEntry {
  code: string
  titre: string
  score_qualite: number | null
  gabarit: string
  nb_champs: number
  paliers: Paliers // comptes ORDINAUX — jamais une « confiance moyenne »
  confiance_min: number | null
  a_anomalies: boolean
  // Révision AFFICHÉE de la fiche dans la file : la validation en lot la
  // renvoie pour que chaque décision porte sur l'état réellement sélectionné.
  // Absente sur un backend antérieur — l'écran la traite alors comme « pas de
  // décision possible sans relecture » (fail closed côté serveur).
  revision?: number
}

export interface FicheDetail {
  code: string
  titre: string
  statut: string
  gabarit: string
  client: string
  bateau: string
  bateau_taille: string
  date_edition: string | null
  nb_fichiers: number
}

export interface HistoriqueFiche {
  action: string
  etat_avant: string | null
  etat_apres: string | null
  commentaire: string | null
  created_at: string
}

export interface FicheListe {
  code: string
  titre: string
  statut: string
  score_qualite: number | null
  gabarit: string
  client: string
  bateau: string
  bateau_taille: string
  nb_fichiers?: number
  a_pdf?: boolean
}

export interface PiecesDeFiche {
  fichier_source?: string | null // compatibilité avec les anciens clients ; ne pas utiliser pour ouvrir un fichier
  pdf_source?: string | null // compatibilité transitoire uniquement
  pieces: PieceJointe[]
}

export type PieceKind = "pdf" | "excel" | "machine" | "other"

export interface PieceJointe {
  id: number
  name: string
  extension: string
  size: number | null
  kind: PieceKind
  is_primary_pdf: boolean
  previewable: boolean
  dossier?: string
  fiche_code?: string
  // Champs historiques maintenus pendant la migration des écrans.
  chemin?: string
  role?: string
  empreinte_sha256?: string
  taille_octets?: number | null
  id_document?: number | null
  nom?: string | null
}

export interface PiecesArchive {
  total: number
  limit: number
  offset: number
  has_more: boolean
  pieces: PieceJointe[]
  extensions: string[]
  dossiers: string[]
}

export interface LotResume {
  id_lot: number
  dossier_racine: string
  statut: string
  nb_dossiers: number
  nb_traites: number
  nb_echecs: number
  progression_pct?: number
}

export interface LotDossierLigne {
  chemin_dossier: string
  statut: string
  raison: string | null
  id_fiche: number | null
  nb_pieces: number
}

export interface LotDetail extends LotResume {
  notes?: string | null
  dossiers: LotDossierLigne[]
  restants: string[]
}

/** Palier ordinal d'un champ (même bornes que compter_par_palier côté moteur). */
export function palierDeChamp(confiance: number | null): keyof Paliers | "non_note" {
  if (confiance == null) return "non_note"
  if (confiance >= 0.99) return "certain"
  if (confiance >= 0.9) return "lu"
  if (confiance >= 0.85) return "decompose"
  return "partiel"
}
