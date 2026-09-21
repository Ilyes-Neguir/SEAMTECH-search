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
}

export interface PiecesDeFiche {
  fichier_source: string | null
  pdf_source: string | null // chemin d'archive du PDF (clé d'idempotence lot C)
  pieces: PieceJointe[]
}

export interface PieceJointe {
  chemin: string
  role: string
  empreinte_sha256: string
  taille_octets: number | null
  id_document: number | null
  nom: string | null
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
