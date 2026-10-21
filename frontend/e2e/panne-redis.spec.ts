import { test, expect, request as pwRequest } from "@playwright/test"
import { createConnection } from "node:net"
import { UI_PASSWORD } from "./helpers"

/**
 * Lot 0a — preuve de non-régression du LOGOUT en panne de Redis.
 *
 * Écart corrigé : `POST /api/auth/logout` répondait « 200 {ok:true,
 * revoquee:false} » quand un sid était présenté mais que la révocation
 * échouait (backend non configuré, injoignable, ou refus non-2xx — Redis
 * indisponible en particulier). Un client lisait donc un succès alors que la
 * session serveur restait vivante. La route répond désormais 503 explicite,
 * sans succès déclaré, tout en effaçant les cookies localement.
 *
 * La panne est RÉELLE : ce test envoie la commande Redis `SHUTDOWN NOSAVE` au
 * serveur qui sert la session. Aucun mock, aucun double, aucune coupure
 * simulée au niveau du client : le port cesse d'accepter les connexions, et
 * le test attend effectivement que ce soit le cas avant d'observer le 503.
 *
 * Pré-requis (vérifiés, jamais supposés) :
 *   * `SEAMTECH_E2E_PANNE_REDIS=1` — ce fichier refuse de s'exécuter sans,
 *     car il arrête le Redis qu'on lui désigne ;
 *   * `SEAMTECH_REDIS_URL` pointe une instance DÉDIÉE, jamais le service
 *     partagé : le port 6379 est refusé pour ne pas couper la CI entière.
 * Le serveur arrêté n'est pas relancé par le test : l'appelant (étape CI /
 * script local) fournit une instance jetable et la recrée après la preuve.
 */

const BASE_URL = process.env.PLAYWRIGHT_TEST_BASE_URL || "http://127.0.0.1:3123"
const REDIS_URL = process.env.SEAMTECH_REDIS_URL ?? ""
const PORT_PARTAGE = 6379
const ACTIF = process.env.SEAMTECH_E2E_PANNE_REDIS === "1"

let portRedis = 0
let hoteRedis = ""
try {
  const url = new URL(REDIS_URL)
  hoteRedis = url.hostname
  portRedis = Number(url.port || "6379")
} catch {
  portRedis = 0
}

const motifDeSaut = !ACTIF
  ? "SEAMTECH_E2E_PANNE_REDIS=1 non positionné : ce test arrête le Redis qu'on lui désigne."
  : !portRedis || (hoteRedis !== "127.0.0.1" && hoteRedis !== "localhost")
    ? `SEAMTECH_REDIS_URL doit viser une instance locale dédiée (reçu : « ${REDIS_URL} »).`
    : portRedis === PORT_PARTAGE
      ? `SEAMTECH_REDIS_URL vise le port partagé ${PORT_PARTAGE} : refus d'arrêter le Redis de toute la suite.`
      : null

test.skip(motifDeSaut !== null, motifDeSaut ?? "")

/** Ouvre une connexion TCP « à la main » : aucune bibliothèque ne cache l'état réel du serveur. */
function connecter(hote: string, port: number): Promise<ReturnType<typeof createConnection>> {
  return new Promise((resolve, reject) => {
    const socket = createConnection({ host: hote, port })
    const minuteur = setTimeout(() => {
      socket.destroy()
      reject(new Error(`Connexion à ${hote}:${port} impossible en 3 s.`))
    }, 3000)
    socket.once("connect", () => {
      clearTimeout(minuteur)
      resolve(socket)
    })
    socket.once("error", (erreur) => {
      clearTimeout(minuteur)
      reject(erreur)
    })
  })
}

/**
 * Arrête le serveur Redis avec sa PROPRE commande d'arrêt (`SHUTDOWN NOSAVE`),
 * puis prouve que le port refuse désormais toute connexion.
 */
async function arreterRedisReel(hote: string, port: number): Promise<void> {
  const socket = await connecter(hote, port)
  await new Promise<void>((resolve, reject) => {
    let reponse = ""
    const minuteur = setTimeout(() => {
      socket.destroy()
      reject(new Error(`Redis ${hote}:${port} n'a pas obéi à SHUTDOWN en 5 s.`))
    }, 5000)
    socket.on("data", (donnees) => {
      reponse += donnees.toString()
    })
    socket.once("error", (erreur) => {
      clearTimeout(minuteur)
      reject(erreur)
    })
    // SHUTDOWN NOSAVE : commande Redis réelle, le serveur s'arrête lui-même.
    socket.once("close", () => {
      clearTimeout(minuteur)
      if (reponse.startsWith("-")) reject(new Error(`Redis a refusé SHUTDOWN : ${reponse.trim()}`))
      else resolve()
    })
    socket.write("*2\r\n$8\r\nSHUTDOWN\r\n$6\r\nNOSAVE\r\n")
  })

  // Preuve d'indisponibilité RÉELLE : plus rien n'écoute sur le port.
  const echeance = Date.now() + 15000
  while (Date.now() < echeance) {
    try {
      const encoreOuvert = await connecter(hote, port)
      encoreOuvert.destroy()
    } catch {
      return // connexion refusée ⇒ Redis est bien indisponible
    }
    await new Promise((resolve) => setTimeout(resolve, 150))
  }
  throw new Error(`Redis ${hote}:${port} accepte encore des connexions : la panne n'est pas réelle.`)
}

async function csrfHeader(context: Awaited<ReturnType<typeof pwRequest.newContext>>): Promise<Record<string, string>> {
  const { cookies } = await context.storageState()
  const jeton = cookies.find((cookie) => cookie.name === "seamtech_csrf")?.value
  return jeton ? { "X-CSRF-Token": jeton } : {}
}

test("Redis réellement arrêté : logout → 503 sans succès déclaré, routes protégées fail-closed", async () => {
  const context = await pwRequest.newContext({ baseURL: BASE_URL })
  try {
    // 1. Connexion réelle : Redis répond, la session existe côté serveur.
    const login = await context.fetch("/api/auth/login", {
      method: "POST",
      data: { mot_de_passe: UI_PASSWORD },
      failOnStatusCode: false,
    })
    expect(login.status(), "la connexion doit aboutir avant la panne").toBe(200)

    const avant = await context.fetch("/api/auth/session", { failOnStatusCode: false })
    expect(avant.status()).toBe(200)
    expect(await avant.json()).toMatchObject({ authenticated: true })

    // 2. Panne RÉELLE — aucune simulation : le serveur Redis s'arrête.
    await arreterRedisReel(hoteRedis, portRedis)

    // 3. Pendant la panne, les routes protégées refusent (fail-closed, 503) :
    //    une session non vérifiable n'est jamais servie.
    for (const chemin of ["/api/search?q=CLIENT", "/api/pieces", "/api/auth/session"]) {
      const refus = await context.fetch(chemin, { failOnStatusCode: false })
      expect(refus.status(), `${chemin} doit échouer fermé quand Redis est arrêté`).toBe(503)
      expect(refus.headers()["cache-control"], chemin).toMatch(/no-store/)
    }

    // 4. Logout : PAS de 200, PAS de succès déclaré — mais déconnexion locale.
    const logout = await context.fetch("/api/auth/logout", {
      method: "POST",
      headers: await csrfHeader(context),
      failOnStatusCode: false,
    })
    expect(logout.status()).toBe(503)
    const corps = await logout.json()
    expect(corps.ok, "le logout ne doit pas se déclarer réussi").not.toBe(true)
    expect(corps.revoquee, "le logout ne doit pas déclarer une révocation").not.toBe(true)
    expect(corps.reason).toBe("revocation_refusee")
    expect(corps.statut_backend).toBe(503)
    expect(String(corps.detail)).toMatch(/session serveur NE l'est PAS/i)

    // 5. Les cookies sont effacés ET Clear-Site-Data est envoyé : le navigateur
    //    est déconnecté localement sans prétendre avoir révoqué le sid.
    const setCookie = (logout.headers()["set-cookie"] ?? "").toLowerCase()
    expect(setCookie).toMatch(/seamtech_session=;/)
    expect(setCookie).toMatch(/path=\//)
    expect(setCookie).toMatch(/httponly/)
    expect(setCookie).toMatch(/samesite=lax/)
    expect(setCookie).toMatch(/max-age=0/)
    expect(setCookie).toMatch(/seamtech_csrf=;/)
    expect(logout.headers()["clear-site-data"]).toMatch(/cookies/)
    expect(logout.headers()["cache-control"]).toMatch(/no-store/)

    // 6. Conséquence observée : le contexte n'a plus de cookie, donc
    //    /api/auth/session répond « non authentifié » sans consulter Redis.
    const apres = await context.fetch("/api/auth/session", { failOnStatusCode: false })
    expect(apres.status()).toBe(200)
    expect(await apres.json()).toMatchObject({ authenticated: false })
  } finally {
    await context.dispose()
  }
})
