import { test, expect } from "@playwright/test"
import { signIn } from "./helpers"

// Phase 1 (2026-09-30) — recherche par dimension : « je tape une dimension dans
// la barre et je vois toutes les voiles correspondantes ».
//
// Données : la VRAIE fiche 7792-SO (SLU 6,60 m — vérité terrain) semée par le
// pipeline de dépôt réel (e2e/seed-live-pg.py → depot.deposer_dossier). Le
// chemin de la valeur est NUMÉRIQUE (tolérance ±0,5 % sur les 7 cotes, filtre
// de cote existant), jamais le tsvector : « 6,60 » coupe en deux lexèmes
// ('6' <-> '60') alors que « 6.60 » reste un float — les deux formes ne se
// croisent pas, la normalisation côté requête converge vers la même valeur.

const CODE_REEL = "7792-SO"

test.describe("Recherche par dimension (Phase 1)", () => {
  // e2e « live » : la recherche de fiches exige PostgreSQL (503 en SQLite) et
  // la fiche 7792-SO n'existe que dans la base seedée par e2e/seed-live-pg.py.
  test.skip(!process.env.SEAMTECH_E2E_DATABASE_URL, "e2e live : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL)")

  test("« 6,60 » retrouve la vraie fiche, et « 660 cm » converge vers la même valeur", async ({ page }) => {
    await signIn(page)

    // Virgule française : la valeur exacte de la SLU réelle.
    await page.goto("/recherche?q=" + encodeURIComponent("6,60"))
    const ligne = page.locator("li", { hasText: CODE_REEL }).first()
    await expect(ligne).toBeVisible({ timeout: 15000 })

    // Point décimal : même valeur, même résultat.
    await page.goto("/recherche?q=" + encodeURIComponent("6.60"))
    await expect(page.locator("li", { hasText: CODE_REEL }).first()).toBeVisible({ timeout: 15000 })

    // Unité différente : « 660 cm » converge vers 6,60 m.
    await page.goto("/recherche?q=" + encodeURIComponent("660 cm"))
    await expect(page.locator("li", { hasText: CODE_REEL }).first()).toBeVisible({ timeout: 15000 })
  })

  test("« SLU 6,60 » oriente vers la cote nommée", async ({ page }) => {
    await signIn(page)
    await page.goto("/recherche?q=" + encodeURIComponent("SLU 6,60"))
    await expect(page.locator("li", { hasText: CODE_REEL }).first()).toBeVisible({ timeout: 15000 })
  })
})
