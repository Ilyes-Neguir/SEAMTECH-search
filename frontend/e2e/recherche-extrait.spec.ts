import { test, expect } from "@playwright/test"
import { signIn } from "./helpers"

// Lot 4a — l'opérateur juge une fiche depuis la liste de résultats grâce à
// un extrait borné et pertinent, sans ouvrir chaque fiche. Le texte source
// est rendu en nœuds React : les correspondances sont de vrais éléments
// <mark>, jamais une chaîne HTML interprétée.

const CODE_REEL = "7792-SO"

test.describe("Recherche — extrait du document (Lot 4a)", () => {
  test.skip(!process.env.SEAMTECH_E2E_DATABASE_URL, "e2e live : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL)")

  test("une recherche texte affiche l'extrait pertinent avec le terme surligné", async ({ page }) => {
    await signIn(page)
    await page.goto("/recherche?q=" + encodeURIComponent("Sailonet"))

    const ligne = page.locator(`li[data-code="${CODE_REEL}"]`)
    await expect(ligne).toBeVisible({ timeout: 15000 })

    const extrait = ligne.getByTestId("fiche-extrait")
    await expect(extrait).toBeVisible()
    await expect(extrait).toContainText(/Extrait du document|Aperçu des champs de la fiche/)
    await expect(extrait.getByTestId("extrait-correspondance").filter({ hasText: /sailonet/i }).first()).toBeVisible()

    const texte = (await extrait.locator("p").textContent()) ?? ""
    expect(texte.length).toBeGreaterThan(0)
    expect(texte.length).toBeLessThanOrEqual(360)
    expect(texte).not.toContain("SEAMTECH_CORRESPONDANCE")
    expect(texte).not.toContain("<mark>")
  })

  test("le parcours sans mot-clé garde un aperçu borné pour comparer les fiches", async ({ page }) => {
    await signIn(page)
    await page.goto("/recherche?q=7792")

    const ligne = page.locator(`li[data-code="${CODE_REEL}"]`)
    await expect(ligne).toBeVisible({ timeout: 15000 })
    const extrait = ligne.getByTestId("fiche-extrait")
    await expect(extrait).toBeVisible()
    await expect(extrait).toHaveAttribute("data-source", /document|chunk|fiche/)
    await expect(extrait.locator("p")).not.toBeEmpty()
  })
})
