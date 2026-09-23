import { test, expect, request as pwRequest } from "@playwright/test"
import { ADMIN, OPERATEUR, UI_PASSWORD } from "./helpers"

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
  { name: "preview", method: "GET", path: "/api/preview?path=/tmp/x.pdf" },
  { name: "open", method: "POST", path: "/api/open?path=/tmp/x.pdf" },
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

      const logout = await context.fetch("/api/auth/logout", { method: "POST", failOnStatusCode: false })
      expect(logout.status()).toBe(200)

      const after = await context.fetch("/api/search?q=CLIENT", { failOnStatusCode: false })
      expect(after.status()).toBe(401)
    } finally {
      await context.dispose()
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

      const deconnexion = await context.fetch("/api/auth/logout", { method: "POST", failOnStatusCode: false })
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
