import { test, expect, request as pwRequest } from "@playwright/test"
import { UI_PASSWORD } from "./helpers"

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
        data: { password: `${UI_PASSWORD}-definitely-wrong` },
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
        data: { password: UI_PASSWORD },
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
        data: { password: UI_PASSWORD },
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
        data: { password: UI_PASSWORD },
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
        data: { password: UI_PASSWORD },
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
