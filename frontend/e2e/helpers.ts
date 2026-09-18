import { expect, type Page } from "@playwright/test"

/**
 * Shared sign-in helper for the e2e suite.
 *
 * Since audit issue #5 (Option B) the whole UI sits behind a session cookie, so
 * every browser-driven spec must authenticate first. The password comes from
 * the same env var the dev server is started with (see playwright.config.ts).
 */
export const UI_PASSWORD = process.env.SEAMTECH_UI_PASSWORD ?? "e2e-shared-password"

export async function signIn(page: Page, password: string = UI_PASSWORD): Promise<void> {
  await page.goto("/login")
  await page.getByLabel("Password").fill(password)
  await page.getByRole("button", { name: "Sign in" }).click()
  // The form router.replace()s to the ?next= target, or "/" by default.
  await page.waitForURL(/\/$/, { timeout: 20000 })
  await expect(page.getByPlaceholder(/Search files, folders/i)).toBeVisible({ timeout: 20000 })
}

export async function signOut(page: Page): Promise<void> {
  await page.getByRole("button", { name: /Sign out/i }).click()
  await page.waitForURL(/\/login/, { timeout: 20000 })
}
