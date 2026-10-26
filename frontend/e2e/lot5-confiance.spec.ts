import { expect, test, type Page } from "@playwright/test"
import { APPROBATEUR, LECTURE, signIn, signInWith } from "./helpers"

/**
 * Lot 5 (Fonction B) — confiance de réutilisation dans le navigateur, contre
 * PostgreSQL réel. La base live sème la vraie fiche 7792-SO (spi) et cinq
 * voiles de référence serrées du même modèle/type (LOT5-POP-*, valeurs
 * ±1 %) : la candidate 7792-SO doit donc obtenir un score « compatible »
 * élevé, AVEC sa justification (médiane/IQR par dimension, sources).
 * L'approbateur évalue et persiste ; la lecture consulte sans pouvoir
 * évaluer ; le compte de secours est refusé.
 */

async function idExtractionParCode(page: Page, codePartiel: string): Promise<number> {
  const reponse = await page.request.get("/api/extractions-voile?limite=100")
  expect(reponse.ok(), "la liste des extractions doit répondre").toBeTruthy()
  const corps = (await reponse.json()) as { extractions: { id_extraction: number; code_fiche: string | null }[] }
  const trouvee = corps.extractions.find((e) => (e.code_fiche ?? "").includes(codePartiel))
  expect(trouvee, `extraction contenant « ${codePartiel} » dans la base live`).toBeTruthy()
  return trouvee!.id_extraction
}

test.describe("Lot 5 — confiance de réutilisation", () => {
  test.skip(
    !process.env.SEAMTECH_E2E_DATABASE_URL,
    "Lot 5 navigateur : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL et comptes nominatifs)",
  )

  test("approbateur : évalue 7792-SO, score compatible justifié et persisté", async ({ page }) => {
    await signInWith(page, APPROBATEUR)
    const idCandidate = await idExtractionParCode(page, "7792")
    await page.getByRole("link", { name: "Confiance" }).click()
    await page.waitForURL(/\/confiance$/, { timeout: 20000 })
    await page.goto(`/confiance?id=${idCandidate}`)

    // La population (candidate incluse) compte 6 voiles ; médiane SLU 6,600.
    const population = page.getByTestId("confiance-population")
    await expect(population).toBeVisible({ timeout: 30000 })
    await expect(population).toContainText("6.600")

    await page.getByRole("button", { name: "Évaluer la confiance" }).click()
    const resultat = page.getByTestId("confiance-resultat")
    await expect(resultat).toBeVisible({ timeout: 30000 })
    await expect(resultat).toContainText("Cohérente avec la population")
    await expect(resultat).toContainText("%")
    // Justification obligatoire : dispersion par dimension + sources.
    await expect(page.getByTestId("confiance-dimensions")).toContainText("SLU (m)")
    await expect(resultat).toContainText("Fiches sources de la population (5)")
    // Persistée : l'historique affiche l'évaluation.
    await expect(page.getByTestId("confiance-historique")).toContainText("N=5")
  })

  test("lecture : consulte population et historique, aucun bouton d'évaluation", async ({ page }) => {
    await signInWith(page, LECTURE)
    const idCandidate = await idExtractionParCode(page, "7792")
    await page.goto(`/confiance?id=${idCandidate}`)
    await expect(page.getByTestId("confiance-population")).toBeVisible({ timeout: 30000 })
    await expect(page.getByRole("button", { name: "Évaluer la confiance" })).toHaveCount(0)
    await expect(page.getByText("Seul un approbateur peut lancer et persister une évaluation.")).toBeVisible()
  })

  test("secours : pas d’identité nominative, écran refusé", async ({ page }) => {
    await signIn(page)
    await page.goto("/confiance")
    await expect(page.getByTestId("acces-refuse")).toBeVisible({ timeout: 20000 })
    await expect(page.getByTestId("confiance-liste")).toHaveCount(0)
  })
})
