"use client"

import { authedFetch } from "@/lib/authed-fetch"

export const PROFONDEUR_CHEMIN_MAX = 32

export interface FichierATeleverser {
  file: File
  rel: string
}

export interface PreparationTeleversement {
  fichiers: FichierATeleverser[]
  ignores: number
  nomRacine: string
  tailleTotale: number
}

export interface EtatTeleversement {
  token?: string
  statut: string
  nom_racine: string
  nombre_fichiers_attendus: number
  taille_totale_attendue_octets: number
  nombre_fichiers_recus: number
  taille_recue_octets: number
  progression_pct: number
  id_lot: number | null
  limites?: {
    fichiers_par_envoi_recommande: number
    taille_envoi_recommandee_octets: number
    fichiers_par_envoi_max: number
    taille_envoi_max_octets: number
    taille_fichier_max_octets: number
    profondeur_chemin_max?: number
  }
}

export interface ReponseCreationLot {
  id_lot: number
  statut?: string
  traitement?: string
  deja_cree?: boolean
}

export interface ProgressionTeleversement {
  phase:
    | "analyse"
    | "creation_session"
    | "transfert"
    | "finalisation"
    | "creation_lot"
    | "termine"
  fichiersRecus: number
  fichiersTotal: number
  octetsRecus: number
  octetsTotal: number
  envoi: number
  envoisTotal: number
  tentative: number
}

type EntreeSysteme = FileSystemEntry

class ErreurHttp extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = "ErreurHttp"
  }
}

function estAbandon(erreur: unknown, signal?: AbortSignal): boolean {
  return Boolean(signal?.aborted) || (erreur instanceof DOMException && erreur.name === "AbortError")
}

function normaliserRelatif(valeur: string, nomFichier: string): string {
  const nettoye = valeur.replaceAll("\\", "/").replace(/^\.\//, "").replace(/^\//, "")
  return nettoye || nomFichier
}

const NOMS_SYSTEME = new Set([".ds_store", "thumbs.db", "desktop.ini"])

function estSystemeOuCache(rel: string): boolean {
  return rel.split("/").some((part) => {
    const bas = part.toLocaleLowerCase("fr-FR")
    return !part || part.startsWith(".") || part.startsWith("~$") || NOMS_SYSTEME.has(bas)
  })
}

async function lireEntree(entry: EntreeSysteme, base: string, sortie: FichierATeleverser[]): Promise<void> {
  if (entry.isFile) {
    const entreeFichier = entry as FileSystemFileEntry
    const fichier = await new Promise<File>((resolve, reject) => entreeFichier.file(resolve, reject))
    sortie.push({ file: fichier, rel: normaliserRelatif(base + fichier.name, fichier.name) })
    return
  }
  if (!entry.isDirectory) return
  const lecteur = (entry as FileSystemDirectoryEntry).createReader()
  for (;;) {
    const entrees = await new Promise<EntreeSysteme[]>((resolve, reject) =>
      lecteur.readEntries(resolve, reject),
    )
    if (entrees.length === 0) break
    for (const enfant of entrees) {
      await lireEntree(enfant, `${base}${entry.name}/`, sortie)
    }
  }
}

/** Énumère récursivement TOUT dossier glissé-déposé avant tout envoi. */
export async function fichiersDepuisDepot(dt: DataTransfer): Promise<FichierATeleverser[]> {
  // webkitGetAsEntry doit être appelé pendant l'évènement de dépôt : on
  // capture donc les entrées avant le premier await.
  const entrees = Array.from(dt.items ?? [])
    .map((item) => item.webkitGetAsEntry?.() ?? null)
    .filter((entry): entry is EntreeSysteme => entry !== null)
  if (entrees.length > 0) {
    const sortie: FichierATeleverser[] = []
    for (const entry of entrees) await lireEntree(entry, "", sortie)
    if (sortie.length > 0) return sortie
  }
  return fichiersDepuisListe(Array.from(dt.files ?? []))
}

export function fichiersDepuisListe(fichiers: File[]): FichierATeleverser[] {
  return fichiers.map((file) => ({
    file,
    rel: normaliserRelatif(file.webkitRelativePath || file.name, file.name),
  }))
}

export function preparerFichiers(bruts: FichierATeleverser[]): PreparationTeleversement {
  const fichiers: FichierATeleverser[] = []
  const vus = new Set<string>()
  let ignores = 0
  let collisions = 0
  let tropProfonds = 0
  for (const item of bruts) {
    const rel = normaliserRelatif(item.rel, item.file.name)
    if (estSystemeOuCache(rel)) {
      ignores += 1
      continue
    }
    if (rel.split("/").length - 1 > PROFONDEUR_CHEMIN_MAX) {
      tropProfonds += 1
      continue
    }
    const cle = rel.normalize("NFC").toLocaleLowerCase("fr-FR")
    if (vus.has(cle)) {
      collisions += 1
      continue
    }
    vus.add(cle)
    fichiers.push({ file: item.file, rel: rel.normalize("NFC") })
  }
  if (collisions > 0) {
    throw new Error(
      `${collisions} chemin(s) en collision (même nom, casse comprise) : corrigez le dossier avant l'envoi.`,
    )
  }
  if (tropProfonds > 0) {
    throw new Error(
      `${tropProfonds} chemin(s) dépassent ${PROFONDEUR_CHEMIN_MAX} niveaux de dossiers : scindez l'arborescence avant l'envoi.`,
    )
  }
  if (fichiers.length === 0) {
    throw new Error("Aucun fichier à envoyer : le dossier est vide ou ne contient que des fichiers système/cachés.")
  }
  fichiers.sort((a, b) => a.rel.localeCompare(b.rel, "fr-FR"))
  const premieres = new Set(fichiers.map((item) => item.rel.split("/")[0]))
  const nomRacine =
    premieres.size === 1 && fichiers.some((item) => item.rel.includes("/"))
      ? [...premieres][0]
      : "Dossier téléversé"
  return {
    fichiers,
    ignores,
    nomRacine,
    tailleTotale: fichiers.reduce((somme, item) => somme + item.file.size, 0),
  }
}

export function decouperEnEnvois(
  fichiers: FichierATeleverser[],
  limites: NonNullable<EtatTeleversement["limites"]>,
): FichierATeleverser[][] {
  const profondeurMax = limites.profondeur_chemin_max ?? PROFONDEUR_CHEMIN_MAX
  if (fichiers.some((item) => item.rel.split("/").length - 1 > profondeurMax)) {
    throw new Error(`Un chemin dépasse la profondeur maximale acceptée (${profondeurMax} dossiers).`)
  }
  const maxFichiers = Math.min(
    limites.fichiers_par_envoi_recommande,
    limites.fichiers_par_envoi_max,
  )
  const maxOctets = Math.min(limites.taille_envoi_recommandee_octets, limites.taille_envoi_max_octets)
  const envois: FichierATeleverser[][] = []
  let courant: FichierATeleverser[] = []
  let octets = 0
  for (const item of fichiers) {
    if (item.file.size > limites.taille_fichier_max_octets) {
      throw new Error(
        `Un fichier dépasse la taille maximale acceptée (${formaterOctets(limites.taille_fichier_max_octets)}).`,
      )
    }
    const depasserait =
      courant.length >= maxFichiers || (courant.length > 0 && octets + item.file.size > maxOctets)
    if (depasserait) {
      envois.push(courant)
      courant = []
      octets = 0
    }
    courant.push(item)
    octets += item.file.size
  }
  if (courant.length > 0) envois.push(courant)
  return envois
}

export function formaterOctets(taille: number): string {
  if (taille < 1024) return `${taille} o`
  if (taille < 1024 * 1024) return `${(taille / 1024).toFixed(1)} Ko`
  if (taille < 1024 * 1024 * 1024) return `${(taille / (1024 * 1024)).toFixed(1)} Mo`
  return `${(taille / (1024 * 1024 * 1024)).toFixed(2)} Go`
}

async function lireJson<T>(response: Response): Promise<T> {
  const corps = (await response.json().catch(() => ({}))) as Record<string, unknown>
  if (!response.ok) {
    const detail = corps.detail
    const message =
      typeof detail === "string"
        ? detail
        : detail
          ? JSON.stringify(detail)
          : `HTTP ${response.status}`
    throw new ErreurHttp(message, response.status)
  }
  return corps as T
}

async function attendre(ms: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) throw new DOMException("Opération annulée.", "AbortError")
  await new Promise<void>((resolve, reject) => {
    const minuteur = setTimeout(resolve, ms)
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(minuteur)
        reject(new DOMException("Opération annulée.", "AbortError"))
      },
      { once: true },
    )
  })
}

async function envoyerEnvoiAvecEssais(
  jeton: string,
  envoi: FichierATeleverser[],
  signal: AbortSignal | undefined,
  annoncerTentative: (tentative: number) => void,
): Promise<EtatTeleversement> {
  let tentative = 0
  for (;;) {
    tentative += 1
    annoncerTentative(tentative)
    try {
      const form = new FormData()
      form.set("chemins", JSON.stringify(envoi.map((item) => item.rel)))
      for (const item of envoi) form.append("fichiers", item.file, item.file.name)
      const response = await authedFetch(
        `/api/imports/dossier/televersements/${encodeURIComponent(jeton)}/fichiers`,
        { method: "POST", body: form, signal },
      )
      return await lireJson<EtatTeleversement>(response)
    } catch (erreur) {
      if (estAbandon(erreur, signal)) throw erreur
      const rejetable =
        erreur instanceof ErreurHttp && ![408, 429, 502, 503, 504].includes(erreur.status) && erreur.status < 500
      if (rejetable || tentative >= 3) throw erreur
      await attendre(tentative === 1 ? 500 : 1500, signal)
    }
  }
}

export async function annulerTeleversement(jeton: string): Promise<void> {
  const response = await authedFetch(
    `/api/imports/dossier/televersements/${encodeURIComponent(jeton)}`,
    { method: "DELETE" },
  )
  await lireJson(response)
}

export async function creerLotDepuisTeleversement(
  jeton: string,
  signal?: AbortSignal,
): Promise<ReponseCreationLot> {
  const response = await authedFetch("/api/imports/dossier/lot", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token_televersement: jeton, recursif: true }),
    signal,
  })
  return lireJson<ReponseCreationLot>(response)
}

/**
 * Téléverse un dossier complet en envois bornés, puis crée UN lot récursif.
 * Aucun envoi ne contient tout le dossier : un incident réseau ne rejoue que
 * l'envoi courant, et le backend reconnaît les octets déjà reçus au SHA-256.
 */
export async function televerserEtCreerLot(
  preparation: PreparationTeleversement,
  options: {
    signal?: AbortSignal
    onSession?: (jeton: string) => void
    onProgression?: (progression: ProgressionTeleversement) => void
  } = {},
): Promise<{ jeton: string; lot: ReponseCreationLot; session: EtatTeleversement }> {
  const { signal, onSession, onProgression } = options
  const annoncer = (progression: ProgressionTeleversement) => onProgression?.(progression)
  const base = {
    fichiersRecus: 0,
    fichiersTotal: preparation.fichiers.length,
    octetsRecus: 0,
    octetsTotal: preparation.tailleTotale,
    envoi: 0,
    envoisTotal: 0,
    tentative: 1,
  }
  annoncer({ ...base, phase: "creation_session" })
  const creation = await authedFetch("/api/imports/dossier/televersements", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      nom_racine: preparation.nomRacine,
      nombre_fichiers: preparation.fichiers.length,
      taille_totale_octets: preparation.tailleTotale,
    }),
    signal,
  })
  const sessionInitiale = await lireJson<EtatTeleversement>(creation)
  if (!sessionInitiale.token || !sessionInitiale.limites) {
    throw new Error("Réponse de création de session de téléversement incomplète.")
  }
  const jeton = sessionInitiale.token
  onSession?.(jeton)

  const envois = decouperEnEnvois(preparation.fichiers, sessionInitiale.limites)
  let session = sessionInitiale
  for (const [index, envoi] of envois.entries()) {
    if (signal?.aborted) throw new DOMException("Opération annulée.", "AbortError")
    session = await envoyerEnvoiAvecEssais(jeton, envoi, signal, (tentative) => {
      annoncer({
        phase: "transfert",
        fichiersRecus: session.nombre_fichiers_recus,
        fichiersTotal: preparation.fichiers.length,
        octetsRecus: session.taille_recue_octets,
        octetsTotal: preparation.tailleTotale,
        envoi: index + 1,
        envoisTotal: envois.length,
        tentative,
      })
    })
    annoncer({
      phase: "transfert",
      fichiersRecus: session.nombre_fichiers_recus,
      fichiersTotal: preparation.fichiers.length,
      octetsRecus: session.taille_recue_octets,
      octetsTotal: preparation.tailleTotale,
      envoi: index + 1,
      envoisTotal: envois.length,
      tentative: 1,
    })
  }

  annoncer({
    phase: "finalisation",
    fichiersRecus: session.nombre_fichiers_recus,
    fichiersTotal: preparation.fichiers.length,
    octetsRecus: session.taille_recue_octets,
    octetsTotal: preparation.tailleTotale,
    envoi: envois.length,
    envoisTotal: envois.length,
    tentative: 1,
  })
  const finalisation = await authedFetch(
    `/api/imports/dossier/televersements/${encodeURIComponent(jeton)}/terminer`,
    { method: "POST", signal },
  )
  session = await lireJson<EtatTeleversement>(finalisation)

  annoncer({
    phase: "creation_lot",
    fichiersRecus: session.nombre_fichiers_recus,
    fichiersTotal: preparation.fichiers.length,
    octetsRecus: session.taille_recue_octets,
    octetsTotal: preparation.tailleTotale,
    envoi: envois.length,
    envoisTotal: envois.length,
    tentative: 1,
  })
  const lot = await creerLotDepuisTeleversement(jeton, signal)
  annoncer({
    phase: "termine",
    fichiersRecus: session.nombre_fichiers_recus,
    fichiersTotal: preparation.fichiers.length,
    octetsRecus: session.taille_recue_octets,
    octetsTotal: preparation.tailleTotale,
    envoi: envois.length,
    envoisTotal: envois.length,
    tentative: 1,
  })
  return { jeton, lot, session }
}
