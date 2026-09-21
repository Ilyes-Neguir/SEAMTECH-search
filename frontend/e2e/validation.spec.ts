import { expect, test, type Page } from "@playwright/test"
import { signIn } from "./helpers"

/** Ouvre /validation et attend la file (au moins une fiche). */
async function ouvrirFile(page: Page) {
  await signIn(page)
  await page.goto("/validation")
  const file = page.getByTestId("file-validation")
  await expect(file).toBeVisible()
  await file.getByTestId("code-fiche").first().waitFor({ timeout: 10000 })
  return file
}

test.describe("validation de bout en bout", () => {
  // e2e « live » : requiert la base PostgreSQL d'é2valuation (sinon ignoré —
  // la suite par défaut tourne sur SQLite et ne couvre pas ce parcours).
  test.skip(!process.env.SEAMTECH_E2E_DATABASE_URL, "e2e live : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL)")

  // Les trois tests s'enchaînent sur la même base e2e (ordre séquentiel garanti
  // au sein d'un describe) : chaque fiche n'est consommée qu'une fois.
  // Base attendue : 7792-SO, 0901-MM, 0902-MM (trois fiches « extrait »).

  test("rejet d'une fiche avec motif obligatoire", async ({ page }) => {
    const file = await ouvrirFile(page)
    await file.getByTestId("code-fiche").first().click()
    await expect(page.getByTestId("titre-fiche")).toBeVisible()

    // sans motif, le rejet est impossible (exigence de traçabilité)
    await expect(page.getByTestId("bouton-rejeter")).toBeDisabled()
    await page.getByTestId("motif-rejet").fill("gabarit douteux — à re-scanner")
    await expect(page.getByTestId("bouton-rejeter")).toBeEnabled()
    await page.getByTestId("bouton-rejeter").click()
    await expect(page.getByTestId("message-ok")).toContainText("rejet", { timeout: 10000 })
  })

  test("correction puis validation individuelle d'une fiche (RG11)", async ({ page }) => {
    const file = await ouvrirFile(page)
    await file.getByTestId("code-fiche").first().click()
    await expect(page.getByTestId("titre-fiche")).toBeVisible()

    // corriger un champ : la saisie fait apparaître le bouton « corriger » ;
    // une valeur corrigée par un humain n'est plus jamais écrasée (RG11)
    const champ = page.getByTestId("champ-fiche.designation")
    await expect(champ).toBeVisible()
    await champ.fill("Spi Asymétrique | Medium Régate — corrigé (e2e)")
    const boutonCorriger = page.getByTestId("bouton-corriger")
    await expect(boutonCorriger).toBeVisible()
    await boutonCorriger.click()
    await expect(page.getByTestId("message-ok")).toContainText("corrigé", { timeout: 10000 })

    // validation individuelle : décision explicite, tracée au journal
    await page.getByTestId("bouton-valider").click()
    await expect(page.getByTestId("message-ok")).toContainText("enregistré", { timeout: 10000 })
  })

  test("validation en lot : verrou de calibration puis acquittement explicite", async ({ page }) => {
    const file = await ouvrirFile(page)
    // cocher UNIQUEMENT les cases de la file (pas celle de l'acquittement)
    const cases = file.locator('input[type="checkbox"]')
    const nombre = await cases.count()
    for (let i = 0; i < nombre; i++) await cases.nth(i).check()
    await page.getByTestId("valider-lot").click()
    // calibration non acquittée → refus explicite (RG11 + verrou §calibration)
    await expect(page.getByTestId("message-erreur")).toContainText("VERROU DE CALIBRATION", { timeout: 10000 })

    // l'acquittement explicite passe le verrou (décision humaine traçée)
    await page.locator('footer input[type="checkbox"]').check()
    await page.getByTestId("valider-lot").click()
    await expect(page.getByTestId("message-ok")).toContainText("Validation en lot", { timeout: 10000 })
    // la file est désormais vide
    await expect(page.getByTestId("file-validation")).toContainText("File vide", { timeout: 10000 })
  })
})
