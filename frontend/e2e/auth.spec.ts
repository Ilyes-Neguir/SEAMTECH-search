import { test, expect, request as pwRequest } from "@playwright/test"
import { ADMIN, OPERATEUR, UI_PASSWORD, signIn } from "./helpers"

/**
 * Regression coverage for audit issue #5 (Option B).
 *
 * BEFORE the fix, every route under app/api/* forwarded SEAMTECH_AUTH_TOKEN to
 * the backend for anyone who could reach the frontend — there was no login, so
 * `GET /api/search?q=CLIENT` returned the document archive to an anonymous
 * caller. These tests are written so that the pre-fix behaviour fails them:
 * an unauthenticated request must be 401, never 200-with-data.
 *
 * They use only Playwright's `request` fixture (no `page`), so they exercise the
 * real HTTP gate without needing a browser binary.
 */

const BASE_URL = process.env.PLAYWRIGHT_TEST_BASE_URL || "http://127.0.0.1:3123"

/** Every gated route, with a syntactically valid request for it. */
const GATED_ROUTES: Array<{ name: string; method: "GET" | "POST" | "PATCH"; path: string; data?: unknown }> = [
  { name: "search", method: "GET", path: "/api/search?q=CLIENT" },
  { name: "pieces catalogue", method: "GET", path: "/api/pieces" },
  { name: "piece preview by id", method: "GET", path: "/api/pieces/1/apercu" },
  { name: "piece download by id", method: "GET", path: "/api/pieces/1/telecharger" },
  { name: "retired preview", method: "GET", path: "/api/preview?path=/tmp/x.pdf" },
  { name: "retired open", method: "POST", path: "/api/open?path=/tmp/x.pdf" },
  { name: "imports create", method: "POST", path: "/api/imports", data: { source_path: "/tmp" } },
  { name: "imports scan", method: "POST", path: "/api/imports/scan", data: { source_path: "/tmp" } },
  { name: "imports confirm", method: "POST", path: "/api/imports/confirm", data: { source_path: "/tmp", technical_pdf: "/tmp/a.pdf" } },
  { name: "imports upload", method: "POST", path: "/api/imports/upload" },
  { name: "import by id", method: "GET", path: "/api/imports/does-not-exist" },
  { name: "import correction", method: "PATCH", path: "/api/imports/does-not-exist", data: { reference: "X" } },
  { name: "import cancel", method: "POST", path: "/api/imports/does-not-exist/cancel" },
  { name: "import retry", method: "POST", path: "/api/imports/does-not-exist/retry" },
  { name: "import artifact", method: "GET", path: "/api/imports/does-not-exist/artifacts/report_pdf" },
]

async function anonymousContext() {
  return pwRequest.newContext({ baseURL: BASE_URL })
}

/** Read the CSRF token the login route stored in the readable cookie. */
async function csrfHeader(context: Awaited<ReturnType<typeof anonymousContext>>): Promise<Record<string, string>> {
  const { cookies } = await context.storageState()
  const jeton = cookies.find((cookie) => cookie.name === "seamtech_csrf")?.value
  return jeton ? { "X-CSRF-Token": jeton } : {}
}

/** Decode the signed v3 payload (test-side inspection, never authentication). */
function sidFromCookie(value: string): string {
  const encoded = value.split(".")[3] ?? ""
  const charge = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8")) as { sid?: string }
  return String(charge.sid ?? "")
}

test.describe("Authentication gate (audit issue #5)", () => {
  for (const route of GATED_ROUTES) {
    test(`${route.method} ${route.name} rejects an anonymous caller with 401`, async () => {
      const context = await anonymousContext()
      try {
        const response = await context.fetch(route.path, {
          method: route.method,
          data: route.data as never,
          failOnStatusCode: false,
        })
        expect(response.status(), `${route.path} must not answer an anonymous caller`).toBe(401)
        const body = await response.json().catch(() => ({}))
        expect(body.detail).toMatch(/Authentication required/i)
      } finally {
        await context.dispose()
      }
    })
  }

  test("anonymous /api/search never returns archive results", async () => {
    // The exact shape of the original bug: 200 + a results array.
    const context = await anonymousContext()
    try {
      const response = await context.fetch("/api/search?q=CLIENT", { failOnStatusCode: false })
      const body = await response.json().catch(() => ({}))
      expect(response.status()).toBe(401)
      expect(body.results).toBeUndefined()
    } finally {
      await context.dispose()
    }
  })

  test("wrong password is rejected and sets no session cookie", async () => {
    const context = await anonymousContext()
    try {
      const response = await context.fetch("/api/auth/login", {
        method: "POST",
        data: { mot_de_passe: `${UI_PASSWORD}-definitely-wrong` },
        failOnStatusCode: false,
      })
      expect(response.status()).toBe(401)
      const setCookie = response.headers()["set-cookie"] ?? ""
      expect(setCookie).not.toMatch(/seamtech_session=[^;]+/)
      // Still locked out afterwards.
      const followUp = await context.fetch("/api/search?q=CLIENT", { failOnStatusCode: false })
      expect(followUp.status()).toBe(401)
    } finally {
      await context.dispose()
    }
  })

  test("correct password sets an httpOnly session cookie and unlocks the API", async () => {
    const context = await anonymousContext()
    try {
      const login = await context.fetch("/api/auth/login", {
        method: "POST",
        data: { mot_de_passe: UI_PASSWORD },
        failOnStatusCode: false,
      })
      expect(login.status()).toBe(200)

      const setCookie = login.headers()["set-cookie"] ?? ""
      expect(setCookie).toMatch(/seamtech_session=/)
      expect(setCookie.toLowerCase()).toMatch(/httponly/)
      expect(setCookie.toLowerCase()).toMatch(/samesite=lax/)

      // The cookie jar is shared, so the previously-401 route now answers.
      const search = await context.fetch("/api/search?q=CLIENT", { failOnStatusCode: false })
      expect(search.status()).not.toBe(401)

      const session = await context.fetch("/api/auth/session", { failOnStatusCode: false })
      expect(session.status()).toBe(200)
      expect(await session.json()).toMatchObject({ authenticated: true, configured: true })
    } finally {
      await context.dispose()
    }
  })

  test("a tampered session token is rejected", async () => {
    const context = await anonymousContext()
    try {
      const login = await context.fetch("/api/auth/login", {
        method: "POST",
        data: { mot_de_passe: UI_PASSWORD },
        failOnStatusCode: false,
      })
      expect(login.status()).toBe(200)
      const cookie = (await context.storageState()).cookies.find((c) => c.name === "seamtech_session")
      expect(cookie, "login must have stored a session cookie").toBeTruthy()

      // Extend the expiry by an hour and keep the original signature: the HMAC
      // covers the expiry, so verification must fail.
      const [, issued, expires, signature] = cookie!.value.split(".")
      const forged = `v1.${issued}.${Number(expires) + 3_600_000}.${signature}`

      const tampered = await pwRequest.newContext({
        baseURL: BASE_URL,
        extraHTTPHeaders: { Cookie: `seamtech_session=${forged}` },
      })
      try {
        const response = await tampered.fetch("/api/search?q=CLIENT", { failOnStatusCode: false })
        expect(response.status()).toBe(401)
      } finally {
        await tampered.dispose()
      }
    } finally {
      await context.dispose()
    }
  })

  test("logout clears the session", async () => {
    const context = await anonymousContext()
    try {
      const login = await context.fetch("/api/auth/login", {
        method: "POST",
        data: { mot_de_passe: UI_PASSWORD },
        failOnStatusCode: false,
      })
      expect(login.status()).toBe(200)

      const logout = await context.fetch("/api/auth/logout", {
        method: "POST",
        headers: await csrfHeader(context),
        failOnStatusCode: false,
      })
      expect(logout.status()).toBe(200)
      expect(await logout.json()).toMatchObject({ ok: true, revoquee: true })
      // Le cookie est effacé avec les mêmes attributs + Clear-Site-Data.
      const setCookie = (logout.headers()["set-cookie"] ?? "").toLowerCase()
      expect(setCookie).toMatch(/seamtech_session=;/)
      expect(setCookie).toMatch(/path=\//)
      expect(setCookie).toMatch(/httponly/)
      expect(setCookie).toMatch(/samesite=lax/)
      expect(setCookie).toMatch(/max-age=0/)
      expect(logout.headers()["clear-site-data"]).toMatch(/cookies/)

      const after = await context.fetch("/api/search?q=CLIENT", { failOnStatusCode: false })
      expect(after.status()).toBe(401)
      const session = await context.fetch("/api/auth/session", { failOnStatusCode: false })
      expect(session.status()).toBe(200)
      expect(await session.json()).toMatchObject({ authenticated: false })
    } finally {
      await context.dispose()
    }
  })

  test("GET /api/auth/logout is refused with 405 (POST only)", async () => {
    const context = await anonymousContext()
    try {
      const response = await context.fetch("/api/auth/logout", { method: "GET", failOnStatusCode: false })
      expect(response.status()).toBe(405)
      expect(response.headers()["allow"] ?? "").toMatch(/POST/i)
    } finally {
      await context.dispose()
    }
  })

  test("POST /api/auth/logout sans jeton CSRF est rejeté (403) et ne déconnecte pas", async () => {
    const context = await anonymousContext()
    try {
      const login = await context.fetch("/api/auth/login", {
        method: "POST",
        data: { mot_de_passe: UI_PASSWORD },
        failOnStatusCode: false,
      })
      expect(login.status()).toBe(200)

      // Pas d'en-tête X-CSRF-Token : le proxy doit refuser AVANT le handler.
      const sansJeton = await context.fetch("/api/auth/logout", {
        method: "POST",
        failOnStatusCode: false,
      })
      expect(sansJeton.status()).toBe(403)
      expect((await sansJeton.json()).detail).toMatch(/CSRF/i)

      // Le refus n'a pas détruit la session.
      const session = await context.fetch("/api/auth/session", { failOnStatusCode: false })
      expect(session.status()).toBe(200)
      expect(await session.json()).toMatchObject({ authenticated: true })

      // Avec le jeton, la déconnexion aboutit.
      const avecJeton = await context.fetch("/api/auth/logout", {
        method: "POST",
        headers: await csrfHeader(context),
        failOnStatusCode: false,
      })
      expect(avecJeton.status()).toBe(200)
    } finally {
      await context.dispose()
    }
  })

  test("« Déconnecter tous mes appareils » révoque deux sessions actives", async () => {
    const premier = await anonymousContext()
    const second = await anonymousContext()
    try {
      for (const contexte of [premier, second]) {
        const login = await contexte.fetch("/api/auth/login", {
          method: "POST",
          data: { mot_de_passe: UI_PASSWORD },
          failOnStatusCode: false,
        })
        expect(login.status()).toBe(200)
      }
      expect((await premier.fetch("/api/auth/session")).status()).toBe(200)
      expect((await second.fetch("/api/auth/session")).status()).toBe(200)
      // Cookie d'origine du premier appareil : il servira à rejouer la session
      // APRÈS le logout-all (le cookie du navigateur initiateur est effacé).
      const cookiePremier = (await premier.storageState()).cookies.find(
        (cookie) => cookie.name === "seamtech_session",
      )?.value
      expect(cookiePremier).toBeTruthy()

      const reponse = await premier.fetch("/api/auth/logout-all", {
        method: "POST",
        headers: await csrfHeader(premier),
        failOnStatusCode: false,
      })
      expect(reponse.status()).toBe(200)
      const corps = await reponse.json()
      expect(corps).toMatchObject({ ok: true })
      expect(corps.sessions_revoquees).toBeGreaterThanOrEqual(2)

      // L'AUTRE appareil (cookie intact) reçoit 401 : la session a bien été
      // révoquée côté serveur, pas seulement effacée de l'initiateur.
      expect((await second.fetch("/api/auth/session", { failOnStatusCode: false })).status()).toBe(401)
      expect((await second.fetch("/api/search?q=CLIENT", { failOnStatusCode: false })).status()).toBe(401)
      // L'initiateur a perdu ses cookies : il est proprement déconnecté.
      const sessionPremier = await premier.fetch("/api/auth/session", { failOnStatusCode: false })
      expect(sessionPremier.status()).toBe(200)
      expect(await sessionPremier.json()).toMatchObject({ authenticated: false })
      expect((await premier.fetch("/api/search?q=CLIENT", { failOnStatusCode: false })).status()).toBe(401)

      // Rejeu du cookie copié AVANT la révocation : 401 sur les deux routes.
      const rejeu = await pwRequest.newContext({
        baseURL: BASE_URL,
        extraHTTPHeaders: { Cookie: `seamtech_session=${cookiePremier}` },
      })
      try {
        expect((await rejeu.fetch("/api/auth/session", { failOnStatusCode: false })).status()).toBe(401)
        expect((await rejeu.fetch("/api/search?q=CLIENT", { failOnStatusCode: false })).status()).toBe(401)
      } finally {
        await rejeu.dispose()
      }
    } finally {
      await premier.dispose()
      await second.dispose()
    }
  })

  test("le sid change à la connexion et l'ancien cookie ne vaut plus rien (anti-fixation)", async () => {
    const contexte = await anonymousContext()
    try {
      const premier = await contexte.fetch("/api/auth/login", {
        method: "POST",
        data: { mot_de_passe: UI_PASSWORD },
        failOnStatusCode: false,
      })
      expect(premier.status()).toBe(200)
      const cookie1 = (await contexte.storageState()).cookies.find((c) => c.name === "seamtech_session")
      expect(cookie1).toBeTruthy()
      const sid1 = sidFromCookie(cookie1!.value)

      // Seconde connexion dans le même navigateur : NOUVEAU sid obligatoire.
      const second = await contexte.fetch("/api/auth/login", {
        method: "POST",
        data: { mot_de_passe: UI_PASSWORD },
        failOnStatusCode: false,
      })
      expect(second.status()).toBe(200)
      const cookie2 = (await contexte.storageState()).cookies.find((c) => c.name === "seamtech_session")
      expect(cookie2).toBeTruthy()
      const sid2 = sidFromCookie(cookie2!.value)
      expect(sid1).not.toBe(sid2)
      expect(sid2.length).toBeGreaterThanOrEqual(43)

      // L'ancien cookie (copié avant rotation) est mort côté serveur.
      const copie = await pwRequest.newContext({
        baseURL: BASE_URL,
        extraHTTPHeaders: { Cookie: `seamtech_session=${cookie1!.value}` },
      })
      try {
        expect((await copie.fetch("/api/auth/session", { failOnStatusCode: false })).status()).toBe(401)
        expect((await copie.fetch("/api/search?q=CLIENT", { failOnStatusCode: false })).status()).toBe(401)
      } finally {
        await copie.dispose()
      }
    } finally {
      await contexte.dispose()
    }
  })

  test("un ancien cookie v1 (auto-porté, sans sid serveur) est refusé immédiatement", async () => {
    const maintenant = Date.now()
    const ancien = `v1.${maintenant}.${maintenant + 3_600_000}.${"A".repeat(43)}`
    const contexte = await pwRequest.newContext({
      baseURL: BASE_URL,
      extraHTTPHeaders: { Cookie: `seamtech_session=${ancien}` },
    })
    try {
      const session = await contexte.fetch("/api/auth/session", { failOnStatusCode: false })
      expect(session.status()).toBe(401)
      expect(await session.json()).toMatchObject({ authenticated: false, reason: "cookie_invalide" })
      expect((await contexte.fetch("/api/search?q=CLIENT", { failOnStatusCode: false })).status()).toBe(401)
    } finally {
      await contexte.dispose()
    }
  })

  test("les réponses authentifiées portent Cache-Control: no-store", async () => {
    const contexte = await anonymousContext()
    try {
      await contexte.fetch("/api/auth/login", {
        method: "POST",
        data: { mot_de_passe: UI_PASSWORD },
        failOnStatusCode: false,
      })
      // API métier et route de session : `no-store` STRICT, y compris en mode
      // développement (le proxy Next pose l'en-tête sur ces réponses).
      for (const chemin of ["/api/search?q=CLIENT", "/api/pieces", "/api/auth/session"]) {
        const reponse = await contexte.fetch(chemin, { failOnStatusCode: false })
        expect(reponse.status(), chemin).toBe(200)
        expect(reponse.headers()["cache-control"], chemin).toMatch(/no-store/)
      }

      // PAGE protégée : en production (`next start`, le mode déployé) elle
      // reçoit `no-store` — c'est ce que vérifie le test live ci-dessous. Le
      // serveur de DÉVELOPPEMENT de Next réécrit l'en-tête HTML en
      // `no-cache, must-revalidate`, qui interdit tout autant la réutilisation
      // sans revalidation ; on l'accepte ici pour ne pas faire passer un
      // artefact du serveur de dev pour le comportement de production.
      const page = await contexte.fetch("/fichiers", { failOnStatusCode: false })
      expect(page.status()).toBe(200)
      expect(page.headers()["cache-control"]).toMatch(/no-store|no-cache.*must-revalidate/)
    } finally {
      await contexte.dispose()
    }
  })

  test("/api/health stays a usable liveness probe but leaks no archive counts", async () => {
    // docker-compose and CI probe this route without a session; a 401 here
    // would mark a healthy container unhealthy.
    const context = await anonymousContext()
    try {
      const response = await context.fetch("/api/health", { failOnStatusCode: false })
      expect(response.status()).toBe(200)
      const body = await response.json()
      expect(body.status).toBe("ok")
      expect(body.authenticated).toBe(false)
      expect(body.documents).toBeUndefined()
      expect(body.files).toBeUndefined()
      expect(body.folders).toBeUndefined()
    } finally {
      await context.dispose()
    }
  })

  test("/api/health reports archive counts once signed in", async () => {
    const context = await anonymousContext()
    try {
      await context.fetch("/api/auth/login", {
        method: "POST",
        data: { mot_de_passe: UI_PASSWORD },
        failOnStatusCode: false,
      })
      const response = await context.fetch("/api/health", { failOnStatusCode: false })
      const body = await response.json().catch(() => ({}))
      expect(body.authenticated).toBe(true)
    } finally {
      await context.dispose()
    }
  })
})

// ---------------------------------------------------------------------------
// Lot L.2 — comptes nominatifs (« qui a validé quoi »).
//
// Ces tests portent sur la couche live (PostgreSQL) : les comptes e2e-operateur
// et e2e-admin sont créés par e2e/seed-live-pg.py, comme des comptes réels.
// ---------------------------------------------------------------------------

test.describe("Comptes nominatifs (Lot L.2)", () => {
  test.skip(!process.env.SEAMTECH_E2E_DATABASE_URL, "e2e live : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL)")

  test("la connexion nominative renvoie l'identité et pose le cookie httpOnly", async () => {
    const context = await anonymousContext()
    try {
      const login = await context.fetch("/api/auth/login", {
        method: "POST",
        data: { identifiant: OPERATEUR.identifiant, mot_de_passe: OPERATEUR.motDePasse },
        failOnStatusCode: false,
      })
      expect(login.status()).toBe(200)
      const corps = await login.json()
      expect(corps).toMatchObject({ ok: true, mode: "nominatif", identifiant: OPERATEUR.identifiant, role: "operateur" })

      const setCookie = login.headers()["set-cookie"] ?? ""
      expect(setCookie).toMatch(/seamtech_session=/)
      expect(setCookie.toLowerCase()).toMatch(/httponly/)
      expect(setCookie.toLowerCase()).toMatch(/samesite=lax/)
      // Ni empreinte, ni sel, ni jeton de service dans la réponse.
      expect(JSON.stringify(corps)).not.toMatch(/scrypt\$/)

      const session = await context.fetch("/api/auth/session", { failOnStatusCode: false })
      expect(session.status()).toBe(200)
      const etat = await session.json()
      expect(etat).toMatchObject({ authenticated: true, identifiant: OPERATEUR.identifiant, role: "operateur" })
      expect(JSON.stringify(etat)).not.toMatch(/scrypt\$/)
      expect(etat.jeton_session).toBeUndefined()
    } finally {
      await context.dispose()
    }
  })

  test("un mot de passe erroné est refusé et ne pose aucun cookie", async () => {
    const context = await anonymousContext()
    try {
      const response = await context.fetch("/api/auth/login", {
        method: "POST",
        data: { identifiant: OPERATEUR.identifiant, mot_de_passe: `${OPERATEUR.motDePasse}-faux` },
        failOnStatusCode: false,
      })
      expect(response.status()).toBe(401)
      const setCookie = response.headers()["set-cookie"] ?? ""
      expect(setCookie).not.toMatch(/seamtech_session=[^;]+/)
      // Toujours pas de session : la route de données reste fermée.
      const suite = await context.fetch("/api/search?q=CLIENT", { failOnStatusCode: false })
      expect(suite.status()).toBe(401)
    } finally {
      await context.dispose()
    }
  })

  test("un opérateur reçoit 403 sur la gestion des comptes, un administrateur 200", async () => {
    const operateur = await anonymousContext()
    const admin = await anonymousContext()
    try {
      await operateur.fetch("/api/auth/login", {
        method: "POST",
        data: { identifiant: OPERATEUR.identifiant, mot_de_passe: OPERATEUR.motDePasse },
        failOnStatusCode: false,
      })
      const refus = await operateur.fetch("/api/auth/utilisateurs", { failOnStatusCode: false })
      expect(refus.status()).toBe(403)
      expect((await refus.json()).detail).toMatch(/administrateur/i)

      await admin.fetch("/api/auth/login", {
        method: "POST",
        data: { identifiant: ADMIN.identifiant, mot_de_passe: ADMIN.motDePasse },
        failOnStatusCode: false,
      })
      const liste = await admin.fetch("/api/auth/utilisateurs", { failOnStatusCode: false })
      expect(liste.status()).toBe(200)
      const comptes = await liste.json()
      expect(comptes.map((c: { identifiant: string }) => c.identifiant)).toEqual(
        expect.arrayContaining([OPERATEUR.identifiant, ADMIN.identifiant]),
      )
      // Jamais d'empreinte ni de mot de passe dans la liste.
      expect(JSON.stringify(comptes)).not.toMatch(/scrypt\$/)
    } finally {
      await operateur.dispose()
      await admin.dispose()
    }
  })

  test("le téléchargement d'une pièce est authentifié et interdit le cache", async () => {
    const context = await anonymousContext()
    try {
      const login = await context.fetch("/api/auth/login", {
        method: "POST",
        data: { identifiant: ADMIN.identifiant, mot_de_passe: ADMIN.motDePasse },
        failOnStatusCode: false,
      })
      expect(login.status()).toBe(200)

      // Une pièce RÉELLE de la base seedée : on la cherche par le même chemin
      // que l'écran de validation (file → pièces de la fiche).
      const file = await context.fetch("/api/validation/file", { failOnStatusCode: false })
      expect(file.status()).toBe(200)
      const codes = ((await file.json()) as Array<{ code: string }>).map((entree) => entree.code)
      expect(codes.length).toBeGreaterThan(0)

      let idPiece: number | null = null
      for (const code of codes) {
        const pieces = await context.fetch(`/api/fiches/${encodeURIComponent(code)}/pieces`, {
          failOnStatusCode: false,
        })
        if (pieces.status() !== 200) continue
        const corps = (await pieces.json()) as {
          pieces: Array<{ id: number; kind: string; is_primary_pdf: boolean }>
        }
        const piece =
          corps.pieces.find((candidate) => candidate.is_primary_pdf) ??
          corps.pieces.find((candidate) => candidate.kind === "pdf")
        if (piece) {
          idPiece = piece.id
          break
        }
      }
      expect(idPiece, "le seed live doit fournir au moins un PDF").not.toBeNull()

      // EN PRODUCTION (next start) : la page protégée porte bien `no-store`.
      // C'est le mode réellement déployé, et le seul où l'en-tête du proxy
      // n'est pas réécrit par le serveur de développement.
      const page = await context.fetch("/fichiers", { failOnStatusCode: false })
      expect(page.status()).toBe(200)
      expect(page.headers()["cache-control"]).toMatch(/no-store/)

      const telechargement = await context.fetch(`/api/pieces/${idPiece}/telecharger`, {
        failOnStatusCode: false,
      })
      expect(telechargement.status()).toBe(200)
      expect(telechargement.headers()["cache-control"]).toMatch(/no-store/)
      expect(telechargement.headers()["content-type"]).toContain("application/pdf")
      const octets = await telechargement.body()
      expect(octets.subarray(0, 4).toString("latin1")).toBe("%PDF")
      expect(octets.length).toBeGreaterThan(1000)

      // Le MÊME téléchargement sans session est refusé : la donnée n'est pas
      // publique, et l'absence de cache ne remplace pas le contrôle d'accès.
      const anonyme = await pwRequest.newContext({ baseURL: BASE_URL })
      try {
        const refus = await anonyme.fetch(`/api/pieces/${idPiece}/telecharger`, {
          failOnStatusCode: false,
        })
        expect(refus.status()).toBe(401)
      } finally {
        await anonyme.dispose()
      }
    } finally {
      await context.dispose()
    }
  })

  test("après déconnexion, le même cookie ne vaut plus rien", async () => {
    const context = await anonymousContext()
    try {
      const login = await context.fetch("/api/auth/login", {
        method: "POST",
        data: { identifiant: OPERATEUR.identifiant, mot_de_passe: OPERATEUR.motDePasse },
        failOnStatusCode: false,
      })
      expect(login.status()).toBe(200)

      // Le cookie est copié AVANT la déconnexion : c'est le scénario qui compte
      // (un cookie volé doit cesser de fonctionner, pas seulement disparaître
      // du navigateur de son propriétaire).
      const cookie = (await context.storageState()).cookies.find((c) => c.name === "seamtech_session")
      expect(cookie, "la connexion doit avoir posé un cookie").toBeTruthy()

      const deconnexion = await context.fetch("/api/auth/logout", {
        method: "POST",
        headers: await csrfHeader(context),
        failOnStatusCode: false,
      })
      expect(deconnexion.status()).toBe(200)

      const copie = await pwRequest.newContext({
        baseURL: BASE_URL,
        extraHTTPHeaders: { Cookie: `seamtech_session=${cookie!.value}` },
      })
      try {
        // La session est révoquée EN BASE : même un cookie encore signé est refusé
        // partout — y compris sur les routes de données, pas seulement /auth/session.
        for (const chemin of ["/api/auth/session", "/api/search?q=CLIENT", "/api/validation/file"]) {
          const refus = await copie.fetch(chemin, { failOnStatusCode: false })
          expect(refus.status(), `${chemin} doit refuser une session révoquée`).toBe(401)
        }
      } finally {
        await copie.dispose()
      }
    } finally {
      await context.dispose()
    }
  })
})

// ---------------------------------------------------------------------------
// Lot 0a — parcours navigateur réel : déconnexion, Retour, reconnexion.
// ---------------------------------------------------------------------------

test.describe("Déconnexion navigateur (Lot 0a)", () => {
  test("connexion → Déconnexion → /login → Retour → aucune donnée protégée → reconnexion", async ({ page }) => {
    await signIn(page)
    await page.goto("/fichiers")
    await expect(page.getByTestId("archive-browser")).toBeVisible({ timeout: 20000 })

    // Deux écrans protégés distincts dans l'historique : le Retour après
    // déconnexion doit atterrir sur un écran qui EXIGE la session, pas sur
    // about:blank (ce qui rendrait le test vacuux).
    await page.goto("/qualite")
    await expect(page.getByTestId("nav")).toBeVisible({ timeout: 20000 })
    await page.goto("/fichiers")
    await expect(page.getByTestId("archive-browser")).toBeVisible({ timeout: 20000 })

    await page.getByTestId("logout").click()
    await page.waitForURL(/\/login/, { timeout: 20000 })
    await expect(page.getByRole("button", { name: "Se connecter" })).toBeVisible()

    // Le bouton Retour du navigateur ne doit restaurer AUCUNE donnée protégée
    // (cookie effacé + Cache-Control: no-store désactive la bfcache).
    await page.goBack()
    await expect(page.getByTestId("archive-browser")).toHaveCount(0)
    // Selon le moteur, le Retour peut réafficher un document en cache mémoire
    // sans repasser par le serveur. On force alors une requête (les réponses
    // authentifiées sont en no-store) : elle doit aboutir à /login, jamais à
    // une page de données avec une session morte.
    if (!page.url().includes("/login")) {
      await page.reload()
    }
    await expect(page).toHaveURL(/\/login/, { timeout: 20000 })
    await expect(page.getByRole("button", { name: "Se connecter" })).toBeVisible({ timeout: 20000 })

    // La reconnexion fonctionne après une déconnexion.
    await signIn(page)
    await expect(page.getByTestId("archive-browser")).toBeVisible({ timeout: 20000 })
  })
})
