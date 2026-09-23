import { expect, test, type Locator, type Page } from "@playwright/test"
import { signIn } from "./helpers"

/** Ouvre /validation et attend la file (au moins une fiche). */
async function ouvrirFile(page: Page): Promise<Locator> {
  await signIn(page)
  await page.goto("/validation")
  const file = page.getByTestId("file-validation")
  await expect(file).toBeVisible()
  await file.getByTestId("code-fiche").first().waitFor({ timeout: 10000 })
  return file
}

/** Sélectionne une fiche de la file PAR SON CODE (indépendant de l'ordre de
 * la file, qui est trié par confiance croissante — l'ordre varie donc avec le
 * gabarit et ne doit jamais être codé en dur dans un test). */
async function ouvrirFiche(page: Page, file: Locator, code: string): Promise<void> {
  // data-code : sélection EXACTE. Un filtre par texte attrapait aussi la ligne
  // du doublon, dont le bandeau cite le code de l'autre fiche (vu en CI).
  await file.locator(`li[data-code="${code}"]`).getByTestId("code-fiche").click()
  await expect(page.getByTestId("titre-fiche")).toHaveText(code)
}

test.describe("validation de bout en bout", () => {
  // e2e « live » : requiert la base PostgreSQL d'évaluation (sinon ignoré —
  // la suite par défaut tourne sur SQLite et ne couvre pas ce parcours).
  test.skip(!process.env.SEAMTECH_E2E_DATABASE_URL, "e2e live : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL)")

  // Les trois tests s'enchaînent sur la même base e2e (ordre séquentiel garanti
  // au sein d'un describe) : chaque fiche n'est consommée qu'une fois.
  // Le seed (e2e/seed-live-pg.py) dépose trois dossiers via le PIPELINE RÉEL
  // (depot → extraction gabarit v2 → a_valider, RG3) :
  //   7792-SO — la VRAIE fiche client (SHA-256 43afc51e…, 842×595 paysage) ;
  //   0901-MM, 0902-MM — fiches synthétiques (CLIENT-GENOA, CLIENT-E2E-TROIS).

  test("le seed dépose trois dossiers et le rejet exige un motif", async ({ page }) => {
    const file = await ouvrirFile(page)
    // Preuve que le pipeline de dépôt a traité les trois dossiers d'exemple.
    for (const code of ["7792-SO", "0901-MM", "0902-MM"]) {
      await expect(file.locator(`li[data-code="${code}"]`)).toBeVisible()
    }

    // Rejet de la fiche synthétique 0902-MM : sans motif, impossible
    // (exigence de traçabilité).
    await ouvrirFiche(page, file, "0902-MM")
    await expect(page.getByTestId("bouton-rejeter")).toBeDisabled()
    await page.getByTestId("motif-rejet").fill("gabarit douteux — à re-scanner")
    await expect(page.getByTestId("bouton-rejeter")).toBeEnabled()
    await page.getByTestId("bouton-rejeter").click()
    await expect(page.getByTestId("message-ok")).toContainText("rejet", { timeout: 10000 })
  })

  test("la vraie fiche 7792-SO : parcours machine complet (< 2 min)", async ({ page }) => {
    await signIn(page)
    // Chrono du PARCOURS MACHINE COMPLET — correction de l'audit indépendant
    // du 22/09 : la fiche 7792-SO est AUTO-SÉLECTIONNÉE à l'arrivée sur la
    // page (validation-app.tsx sélectionne la première de la file, triée par
    // confiance croissante), donc un chrono démarrant APRÈS ouvrirFile()
    // mesurait un plancher (relecture DOM + deux écritures sur une fiche déjà
    // affichée), et le rendu PDF n'était jamais attendu alors que c'est ce que
    // l'opérateur regarde. Le chrono démarre donc AVANT la navigation et la
    // fenêtre exige le rendu réel : file, fiche ouverte, 48 champs, PDF
    // (pdf.js a chargé le document : « 1 / N »), correction RG11, validation.
    // La part humaine (lecture du document, décision) reste hors chrono —
    // elle est mesurée à la main (docs/verite_terrain/MESURE_VALIDATION_2MIN.md).
    const debut = Date.now()
    await page.goto("/validation")
    const file = page.getByTestId("file-validation")
    await expect(file).toBeVisible()
    await file.getByTestId("code-fiche").first().waitFor({ timeout: 10000 })
    // Clic explicite : si l'auto-sélection disparaît un jour, c'est lui qui
    // ouvre réellement la fiche (la charge reste dans la fenêtre de mesure).
    await ouvrirFiche(page, file, "7792-SO")
    // Le rendu du PDF fait partie de la fenêtre : pdf-page affiche « 1 / … »
    // tant que pdf.js n'a pas chargé le document, puis « 1 / N ».
    await expect(page.getByTestId("pdf-page")).toHaveText(/\d+ \/ \d+/, { timeout: 15000 })

    // Les comptages RÉELS mesurés sur le document client (jamais inventés —
    // ce sont les comptes du banc gabarit_test, docs/verite_terrain) :
    // 2 jeux de cotes (dessin ×6, finie ×5), 4 matériaux, 3 galons,
    // 4 jonctions, 3 finitions, 8 options, 3 renforts.
    const parFamille: Array<[string, number]> = [
      ['[data-testid^="champ-cotes.dessin."]', 6],
      ['[data-testid^="champ-cotes.finie."]', 5],
      ['[data-testid^="champ-materiau."]', 4],
      ['[data-testid^="champ-galon."]', 3],
      ['[data-testid^="champ-jonction."]', 4],
      ['[data-testid^="champ-finition."]', 3],
      ['[data-testid^="champ-option."]', 8],
      ['[data-testid^="champ-renfort."]', 3],
    ]
    for (const [selecteur, attendu] of parFamille) {
      await expect(page.locator(selecteur)).toHaveCount(attendu)
    }
    // Valeurs spot du document réel (vérité terrain 7792-SO_ffab.json,
    // mesurées en base le 21/09 — jamais inventées).
    await expect(page.getByTestId("champ-fiche.code")).toHaveValue("7792-SO")
    await expect(page.getByTestId("champ-cotes.finie.slu_m")).toHaveValue("6.6")
    await expect(page.getByTestId("champ-galon.guindant")).toHaveValue("50.0 mm | 65.0 g/m²")

    // Correction d'un champ (RG11 : une valeur corrigée n'est plus écrasée).
    const champ = page.getByTestId("champ-fiche.designation")
    await expect(champ).toBeVisible()
    await champ.fill("Spi Asymétrique | Medium Régate — corrigé (e2e)")
    const boutonCorriger = page.getByTestId("bouton-corriger")
    await expect(boutonCorriger).toBeVisible()
    await boutonCorriger.click()
    await expect(page.getByTestId("message-ok")).toContainText("corrigé", { timeout: 10000 })

    // Validation individuelle : décision explicite, tracée au journal.
    await page.getByTestId("bouton-valider").click()
    await expect(page.getByTestId("message-ok")).toContainText("enregistré", { timeout: 10000 })
    const dureeMs = Date.now() - debut
    test.info().annotations.push({
      type: "mesure-phase1",
      description: `parcours machine complet de la vraie fiche 7792-SO (navigation → fiche ouverte, champs et PDF rendus → correction RG11 → validation) : ${dureeMs} ms ; lecture humaine hors chrono (critère < 120 000 ms)`,
    })
    expect(dureeMs).toBeLessThan(120_000)
  })

  test("validation en lot du reste : verrou de calibration puis acquittement explicite", async ({ page }) => {
    const file = await ouvrirFile(page)
    // Les fiches déjà traitées ont quitté la file (sélection par code EXACT).
    await expect(file.locator('li[data-code="7792-SO"]')).toHaveCount(0)
    await expect(file.locator('li[data-code="0902-MM"]')).toHaveCount(0)
    // cocher UNIQUEMENT les cases de la file (pas celle de l'acquittement)
    const cases = file.locator('input[type="checkbox"]')
    const nombre = await cases.count()
    expect(nombre).toBeGreaterThan(0)
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
