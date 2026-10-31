import { expect, test } from "@playwright/test"
import { APPROBATEUR, signInWith } from "./helpers"

test("Entrée utilise la révision reçue après la saisie du motif", async ({ page }) => {
  test.skip(!process.env.SEAMTECH_E2E_DATABASE_URL, "requiert PostgreSQL et une session approbateur")
  await signInWith(page, APPROBATEUR)
  let liberer!: () => void
  const attente = new Promise<void>(resolve => { liberer = resolve })
  await page.route("**/api/validation/file**", route => route.fulfill({ json: [{
    code: "REVISION-TEST", titre: "Synthèse", score_qualite: 1, gabarit: "TEST", nb_champs: 0,
    paliers: { certain: 0, lu: 0, decompose: 0, partiel: 0 }, confiance_min: null,
    a_anomalies: false, revision: 1,
  }] }))
  await page.route("**/api/validation/doublons", route => route.fulfill({ json: { par_code: {} } }))
  await page.route("**/api/fiches/REVISION-TEST/etat", async route => {
    await attente
    await route.fulfill({ json: { champs: [], revision: 1, statut: "a_valider" } })
  })
  await page.route("**/api/fiches/REVISION-TEST/pieces", route => route.fulfill({ json: { pieces: [], pdf_source: null, fichier_source: null } }))
  let revisionEnvoyee: number | null = null
  await page.route("**/api/fiches/REVISION-TEST/rejeter", route => {
    revisionEnvoyee = route.request().postDataJSON().revision
    return route.fulfill({ json: {} })
  })
  await page.goto("/validation")
  await expect(page.getByTestId("titre-fiche")).toHaveText("REVISION-TEST")
  await page.keyboard.press("r")
  const motif = page.getByTestId("motif-rejet")
  await expect(motif).toBeFocused()
  await motif.fill("motif synthétique")
  await expect(page.getByTestId("validation-app")).not.toHaveAttribute("data-revision", "1")
  liberer()
  await expect(page.getByTestId("validation-app")).toHaveAttribute("data-revision", "1")
  await page.keyboard.press("Enter")
  await expect(page.getByTestId("message-ok")).toContainText("rejeter")
  expect(revisionEnvoyee).toBe(1)
})

test("V utilise la révision reçue après le chargement différé", async ({ page }) => {
  test.skip(!process.env.SEAMTECH_E2E_DATABASE_URL, "requiert PostgreSQL et une session approbateur")
  await signInWith(page, APPROBATEUR)
  let liberer!: () => void
  const attente = new Promise<void>(resolve => { liberer = resolve })
  await page.route("**/api/validation/file**", route => route.fulfill({ json: [{
    code: "REVISION-V", titre: "Synthèse", score_qualite: 1, gabarit: "TEST", nb_champs: 0,
    paliers: { certain: 0, lu: 0, decompose: 0, partiel: 0 }, confiance_min: null,
    a_anomalies: false, revision: 2,
  }] }))
  await page.route("**/api/validation/doublons", route => route.fulfill({ json: { par_code: {} } }))
  await page.route("**/api/fiches/REVISION-V/etat", async route => {
    await attente
    await route.fulfill({ json: { champs: [], revision: 2, statut: "a_valider" } })
  })
  await page.route("**/api/fiches/REVISION-V/pieces", route => route.fulfill({ json: { pieces: [], pdf_source: null, fichier_source: null } }))
  let revisionEnvoyee: number | null = null
  await page.route("**/api/fiches/REVISION-V/valider", route => {
    revisionEnvoyee = route.request().postDataJSON().revision
    return route.fulfill({ json: {} })
  })
  await page.goto("/validation")
  await expect(page.getByTestId("titre-fiche")).toHaveText("REVISION-V")
  await expect(page.getByTestId("validation-app")).not.toHaveAttribute("data-revision", "2")
  liberer()
  await expect(page.getByTestId("validation-app")).toHaveAttribute("data-revision", "2")
  await page.keyboard.press("v")
  await expect(page.getByTestId("message-ok")).toContainText("valider")
  expect(revisionEnvoyee).toBe(2)
})
