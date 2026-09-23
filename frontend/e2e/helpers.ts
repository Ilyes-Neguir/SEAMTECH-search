import { expect, type Page } from "@playwright/test"

/**
 * Shared sign-in helper for the e2e suite.
 *
 * Since audit issue #5 (Option B) the whole UI sits behind a session cookie, so
 * every browser-driven spec must authenticate first.
 *
 * Lot L.2 : the form now asks for an IDENTIFIER plus a password (nominative
 * accounts, « qui a validé quoi »). Leaving the identifier empty keeps the
 * rescue path (the historical shared password) working, which is exactly what
 * `password` alone used to do — so existing specs keep their meaning while the
 * nominative path gets its own specs in auth.spec.ts.
 */
export const UI_PASSWORD = process.env.SEAMTECH_UI_PASSWORD ?? "e2e-shared-password"

/** Nominative accounts seeded by e2e/seed-live-pg.py (live mode only). */
export const OPERATEUR = {
  identifiant: "e2e-operateur",
  motDePasse: process.env.SEAMTECH_E2E_OPERATEUR_PASSWORD ?? "e2e-operateur-password",
}
export const ADMIN = {
  identifiant: "e2e-admin",
  motDePasse: process.env.SEAMTECH_E2E_ADMIN_PASSWORD ?? "e2e-admin-password",
}

export type Identifiants = { identifiant?: string; motDePasse: string }

/** Sign in through the real form (French labels). */
export async function signInWith(page: Page, identifiants: Identifiants): Promise<void> {
  await page.goto("/login")
  if (identifiants.identifiant) {
    await page.getByLabel("Identifiant").fill(identifiants.identifiant)
  }
  await page.getByLabel("Mot de passe").fill(identifiants.motDePasse)
  await page.getByRole("button", { name: "Se connecter" }).click()
  // The form router.replace()s to the ?next= target, or "/" by default.
  await page.waitForURL(/\/$/, { timeout: 20000 })
  await expect(page.getByPlaceholder(/Search files, folders/i)).toBeVisible({ timeout: 20000 })
}

/** Sign in as the rescue account (shared workshop password, no identifier). */
export async function signIn(page: Page, password: string = UI_PASSWORD): Promise<void> {
  await signInWith(page, { motDePasse: password })
}

export async function signOut(page: Page): Promise<void> {
  await page.getByRole("button", { name: /Sign out/i }).click()
  await page.waitForURL(/\/login/, { timeout: 20000 })
}
