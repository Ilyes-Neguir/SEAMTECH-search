import { expect, test } from "@playwright/test"
import { APPROBATEUR, signInWith } from "./helpers"

test("une fiche aux orientations mixtes indique la reprise complète", async ({ page }) => {
  test.skip(!process.env.SEAMTECH_E2E_DATABASE_URL, "requiert PostgreSQL et une session approbateur")
  await signInWith(page, APPROBATEUR)
  await page.route("**/api/validation/file**", route => route.fulfill({
    json: [{ code: "MIXTE-TEST", titre: "Synthèse", gabarit: "TEST", score_qualite: 1,
      nb_champs: 0, paliers: { certain: 0, lu: 0, decompose: 0, partiel: 0 },
      confiance_min: null, a_anomalies: true, orientations_pdf_mixtes: true, revision: 1 }],
  }))
  await page.route("**/api/validation/doublons", route => route.fulfill({ json: { par_code: {} } }))
  await page.route("**/api/fiches/MIXTE-TEST/etat", route => route.fulfill({ json: { champs: [], revision: 1, statut: "a_valider" } }))
  await page.route("**/api/fiches/MIXTE-TEST/pieces", route => route.fulfill({ json: { pdf_source: null, fichier_source: null, pieces: [] } }))
  let decisions = 0
  await page.route("**/api/fiches/MIXTE-TEST/valider", route => { decisions++; return route.fulfill({ json: {} }) })
  await page.goto("/validation")
  const ligne = page.locator('li[data-code="MIXTE-TEST"]')
  await expect(ligne.getByText(/orientations.*mixtes.*relecture complète/i)).toBeVisible()
  await ligne.getByTestId("code-fiche").click()
  await expect(page.getByTestId("validation-app")).toHaveAttribute("data-revision", "1")
  let messageConfirmation = ""
  page.once("dialog", async confirmation => {
    messageConfirmation = confirmation.message()
    await confirmation.dismiss()
  })
  await page.getByTestId("bouton-valider").click()
  expect(messageConfirmation).toMatch(/orientations mixtes.*relisez intégralement/i)
  expect(decisions).toBe(0)
})

test("une orientation non vérifiée impose aussi la relecture complète", async ({ page }) => {
  test.skip(!process.env.SEAMTECH_E2E_DATABASE_URL, "requiert PostgreSQL et une session approbateur")
  await signInWith(page, APPROBATEUR)
  await page.route("**/api/validation/file**", route => route.fulfill({
    json: [{ code: "INCERTAINE-TEST", titre: "Synthèse", gabarit: "TEST", score_qualite: 1,
      nb_champs: 0, paliers: { certain: 0, lu: 0, decompose: 0, partiel: 0 },
      confiance_min: null, a_anomalies: true, orientations_pdf_mixtes: false,
      orientations_pdf_non_verifiees: true, revision: 1 }],
  }))
  await page.route("**/api/validation/doublons", route => route.fulfill({ json: { par_code: {} } }))
  await page.route("**/api/fiches/INCERTAINE-TEST/etat", route => route.fulfill({ json: { champs: [], revision: 1, statut: "a_valider" } }))
  await page.route("**/api/fiches/INCERTAINE-TEST/pieces", route => route.fulfill({ json: { pdf_source: null, fichier_source: null, pieces: [] } }))
  let decisions = 0
  await page.route("**/api/fiches/INCERTAINE-TEST/valider", route => { decisions++; return route.fulfill({ json: {} }) })
  await page.goto("/validation")
  const ligne = page.locator('li[data-code="INCERTAINE-TEST"]')
  await expect(ligne.getByText(/orientation.*non vérifiée.*relecture complète/i)).toBeVisible()
  await ligne.getByTestId("code-fiche").click()
  await expect(page.getByTestId("validation-app")).toHaveAttribute("data-revision", "1")
  let messageConfirmation = ""
  page.once("dialog", async confirmation => {
    messageConfirmation = confirmation.message()
    await confirmation.dismiss()
  })
  await page.getByTestId("bouton-valider").click()
  expect(messageConfirmation).toMatch(/orientation.*non vérifiée.*relisez intégralement/i)
  expect(decisions).toBe(0)
})
