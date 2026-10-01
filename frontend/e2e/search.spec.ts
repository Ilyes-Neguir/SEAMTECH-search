import { test, expect } from "@playwright/test"
import { signIn } from "./helpers"

test.describe("Search Workflow", () => {
  test("should search for documents and display results with preview", async ({ page }) => {
    await signIn(page)

    // Le catalogue de pièces est l’écran Fichiers par défaut ; la recherche
    // historique reste disponible sans perte dans le mode « Recherche avancée ».
    await page.goto("/fichiers")
    await page.getByRole("button", { name: "Recherche avancée" }).click()

    const searchInput = page.getByPlaceholder(/Rechercher des fichiers/i)
    await expect(searchInput).toBeVisible()

    const reponseRecherche = await page.request.get(`/api/search?q=${encodeURIComponent("fiche-7792-SO_ffab.pdf")}`)
    expect(reponseRecherche.ok()).toBeTruthy()
    const chargeRecherche = await reponseRecherche.json()
    expect(chargeRecherche.results[0]).toHaveProperty("id")
    for (const ligne of chargeRecherche.results) {
      expect(ligne).not.toHaveProperty("path")
      expect(ligne).not.toHaveProperty("path_key")
      expect(ligne).not.toHaveProperty("object_key")
    }

    await searchInput.fill("fiche-7792-SO_ffab.pdf")
    await searchInput.press("Enter")

    // Expect results to be displayed
    const resultsContainer = page.locator("main")
    await expect(resultsContainer).toBeVisible()

    // Wait for at least one result item
    const firstResult = page.locator('[role="button"][tabindex="0"]').filter({ hasText: /fiche-7792-SO_ffab\.pdf/i }).first()
    await expect(firstResult).toBeVisible({ timeout: 10000 })

    // L’aperçu et le téléchargement passent par l’identifiant du catalogue.
    await firstResult.click()
    await expect(page.locator('[data-testid="visionneuse-piece"]')).toBeVisible()
    await expect(page.getByRole("link", { name: "Télécharger le fichier" })).toBeVisible()
  })
})
