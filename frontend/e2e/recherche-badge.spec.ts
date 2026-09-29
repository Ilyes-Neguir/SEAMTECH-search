import { test, expect } from "@playwright/test"
import { signIn } from "./helpers"

// Contrat 2026-09-29 (production-readiness Phase 2.1) : les fiches A_VALIDER
// sont cherchables PAR DÉFAUT, badgées « Non vérifiée » ; le filtre « Validées
// uniquement » (inclure_a_valider=false) les retire. Le statut n'est jamais
// masqué. Données : le seed crée BIS-7792 (copie a_valider de 7792-SO).

const CODE_A_VALIDER = "BIS-7792"
const CODE_VALIDEE = "7792-SO"

test.describe("Recherche — badge « Non vérifiée » et archive de confiance", () => {
  // e2e « live » : la recherche de fiches exige PostgreSQL (503 en SQLite) et
  // BIS-7792 n'existe que dans la base seedée par e2e/seed-live-pg.py.
  test.skip(!process.env.SEAMTECH_E2E_DATABASE_URL, "e2e live : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL)")

  test("une fiche a_valider sort par défaut, badgée, et disparaît avec « Validées uniquement »", async ({ page }) => {
    await signIn(page)
    await page.goto("/recherche?q=7792")

    // Par défaut : la fiche a_valider EST présente, avec son badge.
    const ligneAValider = page.locator("li", { hasText: CODE_A_VALIDER }).first()
    await expect(ligneAValider).toBeVisible({ timeout: 15000 })
    await expect(ligneAValider.getByTestId("badge-non-verifiee")).toBeVisible()
    await expect(ligneAValider).toContainText("Non vérifiée")

    // La fiche validée, elle, ne porte PAS de badge.
    const ligneValidee = page.locator("li", { hasText: CODE_VALIDEE }).first()
    await expect(ligneValidee).toBeVisible({ timeout: 15000 })
    await expect(ligneValidee.getByTestId("badge-non-verifiee")).toHaveCount(0)

    // Le filtre « Validées uniquement » retire la fiche a_valider…
    const caseArchive = page.getByTestId("case-validees-uniquement")
    await expect(caseArchive).toBeVisible()
    await caseArchive.check()
    await expect(page.locator("li", { hasText: CODE_A_VALIDER })).toHaveCount(0, { timeout: 15000 })
    await expect(ligneValidee).toBeVisible()
    // …et l'état est partageable dans l'URL.
    await expect(page).toHaveURL(/inclure_a_valider=false/, { timeout: 5000 })
    await expect(page.getByTestId("pastille-validees-uniquement")).toBeVisible()

    // Décocher ré-affiche la fiche badgée (l'état n'est jamais caché).
    await caseArchive.uncheck()
    await expect(page.locator("li", { hasText: CODE_A_VALIDER }).first()).toBeVisible({ timeout: 15000 })
    await expect(page.locator("li", { hasText: CODE_A_VALIDER }).first().getByTestId("badge-non-verifiee")).toBeVisible()
  })
})
