import { expect, test, type Page } from "@playwright/test"
import { APPROBATEUR, LECTURE, signIn, signInWith } from "./helpers"

/**
 * Lot 4b (Fonction A) — comparaison de voiles bloc par bloc dans le
 * navigateur, contre PostgreSQL réel (base live semée : 7792-SO spi 29er,
 * génois Sun 2000…). Le parcours prouve : sélection A/B, écarts réels en cm
 * (SLU 6,60 m contre 8,20 m ⇒ +160 cm), et l'honnêteté de l'écran — modèles
 * différents ⇒ badge « indicative » + avertissement, jamais « même modèle ».
 * La lecture seule suffit (lecture y a accès) ; le compte de secours, sans
 * identité nominative, est refusé.
 */

async function idExtractionParCode(page: Page, codePartiel: string): Promise<number> {
  const reponse = await page.request.get("/api/extractions-voile?limite=100")
  expect(reponse.ok(), "la liste des extractions doit répondre").toBeTruthy()
  const corps = (await reponse.json()) as { extractions: { id_extraction: number; code_fiche: string | null }[] }
  const trouvee = corps.extractions.find((e) => (e.code_fiche ?? "").includes(codePartiel))
  expect(trouvee, `extraction contenant « ${codePartiel} » dans la base live`).toBeTruthy()
  return trouvee!.id_extraction
}

test.describe("Lot 4b — comparaison de voiles", () => {
  test.skip(
    !process.env.SEAMTECH_E2E_DATABASE_URL,
    "Lot 4b navigateur : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL et comptes nominatifs)",
  )

  test("approbateur : A/B choisis à l’écran, écarts cm réels, modèles différents dits franchement", async ({ page }) => {
    await signInWith(page, APPROBATEUR)
    await page.getByRole("link", { name: "Comparaison" }).click()
    await page.waitForURL(/\/comparaison$/, { timeout: 20000 })

    await page.getByTestId("recherche-a").fill("7792")
    const choixA = page.getByTestId("liste-a").getByTestId("extraction-choix").first()
    await expect(choixA).toContainText("7792", { timeout: 30000 })
    await choixA.click()

    const choixB = page
      .getByTestId("liste-b")
      .getByTestId("extraction-choix")
      .filter({ hasText: "0901" })
      .first()
    await expect(choixB).toBeVisible({ timeout: 30000 })
    await choixB.click()

    await expect(page.getByTestId("tableau-dimensions")).toBeVisible({ timeout: 30000 })
    // SLU : 8,20 m (génois Sun 2000) − 6,60 m (7792-SO) = +160 cm, valeurs réelles du seed.
    await expect(page.getByTestId("delta-cm-slu_m")).toContainText("160")
    await expect(page.getByTestId("badge-comparable")).toHaveAttribute("data-comparable", "non")
    await expect(page.getByTestId("avertissements-comparaison")).toContainText("DIFFÉRENTS")
    await expect(page.getByTestId("bloc-dimensions")).toBeVisible()
    await expect(page.getByTestId("bloc-identification")).toBeVisible()
    await expect(page.getByTestId("bloc-qualite")).toBeVisible()
  })

  test("lecture : comparaison en lecture seule via lien profond ?a=&b=", async ({ page }) => {
    await signInWith(page, LECTURE)
    const idA = await idExtractionParCode(page, "7792")
    const idB = await idExtractionParCode(page, "0901")
    await page.goto(`/comparaison?a=${idA}&b=${idB}`)
    await expect(page.getByTestId("resultat-comparaison")).toBeVisible({ timeout: 30000 })
    await expect(page.getByTestId("delta-pct-slu_m")).toContainText("%")
    const ligneSf = page.getByTestId("ligne-dimension").filter({ hasText: "SF" }).first()
    await expect(ligneSf).toBeVisible()
  })

  test("secours : pas d’identité nominative, écran refusé", async ({ page }) => {
    await signIn(page)
    await page.goto("/comparaison")
    await expect(page.getByTestId("acces-refuse")).toBeVisible({ timeout: 20000 })
    await expect(page.getByTestId("selecteurs")).toHaveCount(0)
  })
})
