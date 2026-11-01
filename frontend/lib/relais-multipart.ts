/**
 * Relais multipart vers le backend SEAMTECH — durci.
 *
 * Constat de la campagne du 28/10 (P0) : les routes BFF qui reçoivent un envoi
 * de dossier faisaient `await req.formData()` puis renvoyaient le `FormData`
 * au backend. Quatre conséquences mesurables :
 *
 * 1. **Débordement** — tout l'envoi était matérialisé en mémoire du processus
 *    Next (objets `Blob` compris), sans plafond propre au BFF : un envoi de
 *    512 Mo (borne acceptée par le backend) occupait ~1 Go, et rien n'arrêtait
 *    un corps de 10 Go avant épuisement mémoire du BFF ;
 * 2. **Corps malformé** — un multipart tronqué faisait lever `formData()`,
 *    rattrapé par le `catch` générique qui répondait **502 « backend
 *    injoignable »** : message faux, mauvais code, et l'utilisateur renvoyait
 *    un envoi que le backend aurait refusé en 400 ;
 * 3. **Interruption** — si le navigateur abandonnait (onglet fermé, réseau),
 *    le BFF avait déjà tout lu et poursuivait l'envoi vers le backend : une
 *    session de téléversement grossissait pour un client parti ;
 * 4. **Réponse** — `await response.json()` était appelé avant de recopier le
 *    code : une page d'erreur HTML du backend (502 de proxy, 500 sans corps)
 *    devenait un 502 BFF, en perdant le vrai statut.
 *
 * Ce module relaie donc **en flux**, sans jamais matérialiser le corps :
 *
 * - refus 400 si l'en-tête n'est pas un multipart avec frontière ;
 * - refus 413 avant lecture si `Content-Length` dépasse la borne, et 413 en
 *   cours de flux si le corps dépasse la borne sans l'annoncer ;
 * - abandon du backend si le client disparaît (`AbortSignal` du client) ;
 * - abandon + 504 si le backend ne répond plus (inactivité) ;
 * - 502 seulement pour une VRAIE panne de liaison, message générique (aucun
 *   détail interne renvoyé au navigateur) ;
 * - statut et corps du backend recopiés tels quels, JSON ou non ;
 * - limite d'envois simultanés par session : au-delà, 429 immédiat, plutôt
 *   que deux flux concurrents qui se disputent la mémoire et le disque.
 *
 * Le module est volontairement sans dépendance à Next : il prend une
 * `Request` standard et rend une `Response` standard, ce qui le rend testable
 * hors serveur (voir `relais-multipart.test.ts`).
 */

/** Borne par défaut : le backend accepte 512 Mo par envoi (multipart compris). */
export const LIMITE_ENVOI_OCTETS = 512 * 1024 * 1024
/** Marge pour l'enveloppe multipart (frontières, en-têtes de parties). */
export const MARGE_MULTIPART_OCTETS = 16 * 1024 * 1024
/** Inactivité tolérée du backend avant abandon (envoi ET réponse). */
export const DELAI_INACTIVITE_MS = 120_000
/** Envois simultanés tolérés pour une même session de téléversement. */
export const RELAIS_SIMULTANES_MAX = 2

export interface OptionsRelais {
  /** Taille maximale acceptée pour le corps HTTP, en octets. */
  limiteOctets?: number
  /** Inactivité tolérée avant abandon, en millisecondes (0 = pas de limite). */
  delaiInactiviteMs?: number
  /** Clé de concurrence (jeton de session) — `null` pour une limite globale. */
  cleConcurrence?: string | null
  /** Envois simultanés tolérés pour cette clé. */
  simultanesMax?: number
  /** Injectable pour les tests. */
  fetchImpl?: typeof fetch
}

export type ResultatRelais =
  | { type: "reponse"; reponse: Response }
  | { type: "refus"; statut: number; detail: string }
  | { type: "abandon" }

/** Compteurs de concurrence, par clé. Vidés par `reinitialiserRelais()`. */
const relaisEnCours = new Map<string, number>()

export function reinitialiserRelais(): void {
  relaisEnCours.clear()
}

export function relaisEnCoursPour(cle: string): number {
  return relaisEnCours.get(cle) ?? 0
}

function acquerir(cle: string, maximum: number): boolean {
  const courant = relaisEnCours.get(cle) ?? 0
  if (courant >= maximum) return false
  relaisEnCours.set(cle, courant + 1)
  return true
}

function relacher(cle: string): void {
  const courant = relaisEnCours.get(cle) ?? 0
  if (courant <= 1) relaisEnCours.delete(cle)
  else relaisEnCours.set(cle, courant - 1)
}

/** Une panne de liaison : le seul cas qui justifie un 502. */
class ErreurLiaison extends Error {}

/** Le corps dépasse la borne : 413, jamais 502. */
class ErreurTropGrand extends Error {}

/** Le client a disparu : rien à répondre, tout est déjà annulé. */
class ErreurAbandon extends Error {}

/** Le backend ne progresse plus : 504. */
class ErreurInactivite extends Error {}

function frontiereMultipart(entete: string | null): string | null {
  if (!entete) return null
  const type = entete.split(";")[0]?.trim().toLowerCase()
  if (type !== "multipart/form-data") return null
  const trouvee = /;\s*boundary=(?:"([^"]+)"|([^;\s]+))/i.exec(entete)
  const frontiere = trouvee?.[1] ?? trouvee?.[2] ?? ""
  // Une frontière vide ou démesurée n'est pas un multipart exploitable.
  return frontiere.length >= 1 && frontiere.length <= 200 ? frontiere : null
}

/**
 * Enveloppe le corps de la requête d'un compteur d'octets qui coupe le flux
 * dès le dépassement — sans jamais accumuler les morceaux reçus.
 */
function bornerCorps(
  corps: ReadableStream<Uint8Array> | null,
  limite: number,
  onOctets: (total: number) => void,
): ReadableStream<Uint8Array> | null {
  if (!corps) return null
  let total = 0
  return corps.pipeThrough(
    new TransformStream<Uint8Array, Uint8Array>({
      transform(morceau, controleur) {
        total += morceau.byteLength
        onOctets(total)
        if (total > limite) {
          controleur.error(new ErreurTropGrand("Corps de requête au-delà de la borne acceptée."))
          return
        }
        controleur.enqueue(morceau)
      },
    }),
  )
}

function messageDe(erreur: unknown): string {
  return erreur instanceof Error ? erreur.message : String(erreur)
}

function estAbandon(erreur: unknown): boolean {
  return (
    erreur instanceof ErreurAbandon ||
    (erreur instanceof Error && (erreur.name === "AbortError" || erreur.name === "TimeoutError"))
  )
}

/**
 * Relaie `req` vers `url` en flux et rend une réponse standard.
 *
 * `entetes` doit contenir les en-têtes d'authentification ; l'en-tête
 * `content-type` du client est recopié ici (frontière comprise) et ne doit
 * donc pas être fourni par l'appelant.
 */
export async function relayerMultipart(
  req: Request,
  url: string,
  entetes: Record<string, string>,
  options: OptionsRelais = {},
): Promise<ResultatRelais> {
  const limite = options.limiteOctets ?? LIMITE_ENVOI_OCTETS + MARGE_MULTIPART_OCTETS
  const delai = options.delaiInactiviteMs ?? DELAI_INACTIVITE_MS
  const cle = options.cleConcurrence ?? null
  const simultanesMax = options.simultanesMax ?? RELAIS_SIMULTANES_MAX
  const envoyer = options.fetchImpl ?? fetch

  const typeContenu = req.headers.get("content-type")
  if (!frontiereMultipart(typeContenu)) {
    // Ne pas laisser un corps non lu accroché à la connexion.
    await req.body?.cancel().catch(() => undefined)
    return {
      type: "refus",
      statut: 400,
      detail: "Envoi multipart attendu (multipart/form-data avec frontière).",
    }
  }

  const annonce = req.headers.get("content-length")
  if (annonce !== null && /^\d+$/.test(annonce.trim())) {
    const octets = Number(annonce.trim())
    if (Number.isFinite(octets) && octets > limite) {
      await req.body?.cancel().catch(() => undefined)
      return {
        type: "refus",
        statut: 413,
        detail: `Envoi trop volumineux : ${octets} octets annoncés, borne ${limite}. Découpez le dossier.`,
      }
    }
  }

  if (cle !== null && !acquerir(cle, simultanesMax)) {
    await req.body?.cancel().catch(() => undefined)
    return {
      type: "refus",
      statut: 429,
      detail: "Trop d'envois simultanés pour cette session : attendez la fin de l'envoi en cours.",
    }
  }

  const controleur = new AbortController()
  let minuterie: ReturnType<typeof setTimeout> | null = null
  let inactif = false
  const relancer = () => {
    if (delai <= 0) return
    if (minuterie !== null) clearTimeout(minuterie)
    minuterie = setTimeout(() => {
      inactif = true
      controleur.abort(new ErreurInactivite("Backend inactif."))
    }, delai)
  }
  const arreterMinuterie = () => {
    if (minuterie !== null) clearTimeout(minuterie)
    minuterie = null
  }

  const surAbandonClient = () => controleur.abort(new ErreurAbandon("Client parti."))
  if (req.signal.aborted) {
    if (cle !== null) relacher(cle)
    return { type: "abandon" }
  }
  req.signal.addEventListener("abort", surAbandonClient, { once: true })

  try {
    relancer()
    const corps = bornerCorps(req.body, limite, relancer)
    const entetesRequete: Record<string, string> = { ...entetes, "content-type": typeContenu as string }
    if (annonce !== null && /^\d+$/.test(annonce.trim())) {
      entetesRequete["content-length"] = annonce.trim()
    }
    const reponse = await envoyer(url, {
      method: req.method,
      headers: entetesRequete,
      body: corps,
      signal: controleur.signal,
      cache: "no-store",
      // Exigé par undici/Node pour un corps en flux.
      ...({ duplex: "half" } as Record<string, unknown>),
    })
    arreterMinuterie()

    const entetesReponse: Record<string, string> = {
      "cache-control": "no-store",
      "content-type": reponse.headers.get("content-type") ?? "application/json",
    }
    // Le corps du backend est recopié en flux : pas de `.json()` ici, une page
    // d'erreur HTML doit arriver telle quelle avec son VRAI statut.
    return { type: "reponse", reponse: new Response(reponse.body, { status: reponse.status, headers: entetesReponse }) }
  } catch (erreur) {
    arreterMinuterie()
    if (erreur instanceof ErreurTropGrand) {
      return {
        type: "refus",
        statut: 413,
        detail: `Envoi trop volumineux : borne de ${limite} octets dépassée. Découpez le dossier.`,
      }
    }
    if (inactif || erreur instanceof ErreurInactivite) {
      return { type: "refus", statut: 504, detail: "Le backend ne répond plus : envoi interrompu, réessayez." }
    }
    if (estAbandon(erreur) || req.signal.aborted) return { type: "abandon" }
    // Ici seulement : liaison réellement rompue. Aucun détail interne renvoyé.
    console.error(`[relais] envoi vers ${url} impossible : ${messageDe(erreur)}`)
    return { type: "refus", statut: 502, detail: "Backend SEAMTECH injoignable." }
  } finally {
    req.signal.removeEventListener("abort", surAbandonClient)
    if (cle !== null) relacher(cle)
  }
}
