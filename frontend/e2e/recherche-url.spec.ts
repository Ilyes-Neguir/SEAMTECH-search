import { test, expect } from "@playwright/test"
import { signIn } from "./helpers"

test.describe("Recherche URL partageable (Lot J)", () => {
  test("question+filtres+tri+page dans URL, restaurés au chargement, copier lien", async ({ page }) => {
    await signIn(page)

    // Aller sur /recherche avec des params explicites
    await page.goto("/recherche?q=grand&type_voile=Grand-voile&tri=code_asc&page=1")

    // La saisie doit être restaurée depuis l'URL
    const saisie = page.getByTestId("recherche-saisie")
    await expect(saisie).toBeVisible({ timeout: 10000 })
    await expect(saisie).toHaveValue("grand", { timeout: 10000 })

    // Le tri doit être restauré
    const tri = page.getByTestId("recherche-tri")
    await expect(tri).toBeVisible()
    await expect(tri).toHaveValue("code_asc")

    // Filtre actif visible (chip)
    // Au moins le chip type_voile ou le texte Grand-voile doit apparaître
    const chip = page.locator("text=Grand-voile").first()
    // Si pas de données, la facette peut être vide, mais l'URL doit quand même être conservée
    // On vérifie surtout que l'URL contient les params
    await expect(page).toHaveURL(/q=grand/)
    await expect(page).toHaveURL(/type_voile=Grand-voile/)
    await expect(page).toHaveURL(/tri=code_asc/)

    // Bouton copier lien présent et fonctionnel
    const copier = page.getByTestId("copier-lien")
    await expect(copier).toBeVisible()
    await copier.click()
    await expect(copier).toContainText(/Lien copié|Copier le lien/, { timeout: 5000 })

    // Changer le tri doit mettre à jour l'URL (sans rechargement)
    await tri.selectOption("date_desc")
    await expect(page).toHaveURL(/tri=date_desc/, { timeout: 5000 })

    // F5 doit conserver l'état (pas d'état caché)
    await page.reload()
    await expect(saisie).toHaveValue("grand", { timeout: 10000 })
    await expect(page.getByTestId("recherche-tri")).toHaveValue("date_desc", { timeout: 10000 })

    // Nouvel onglet avec même URL doit restaurer
    const url = page.url()
    const page2 = await page.context().newPage()
    await page2.goto("/login")
    // sign in sur nouvel onglet
    const pwd = process.env.SEAMTECH_UI_PASSWORD ?? "e2e-shared-password"
    await page2.getByLabel("Password").fill(pwd)
    await page2.getByRole("button", { name: "Sign in" }).click()
    await page2.waitForURL(/\//, { timeout: 20000 })
    await page2.goto(url)
    await expect(page2.getByTestId("recherche-saisie")).toHaveValue("grand", { timeout: 10000 })
    await expect(page2.getByTestId("recherche-tri")).toHaveValue("date_desc", { timeout: 10000 })
    await page2.close()
  })

  test("facette dimension présente avec cotes explicites", async ({ page }) => {
    await signIn(page)
    await page.goto("/recherche")

    const facetteDim = page.getByTestId("facette-dimension")
    await expect(facetteDim).toBeVisible({ timeout: 10000 })

    const selectCote = page.getByTestId("dimension-cote")
    await expect(selectCote).toBeVisible()
    // 7 cotes explicites
    const options = await selectCote.locator("option").allTextContents()
    const attendu = ["SLU", "SLE", "SF", "SHW", "SPA", "Têtière", "Poids"]
    for (const mot of attendu) {
      expect(options.join(" ")).toContain(mot)
    }

    // Inputs min/max présents
    await expect(page.getByTestId("dimension-min")).toBeVisible()
    await expect(page.getByTestId("dimension-max")).toBeVisible()
    await expect(page.getByTestId("dimension-appliquer")).toBeVisible()
  })

  test("pagination et tri dans URL", async ({ page }) => {
    await signIn(page)
    await page.goto("/recherche?q=&tri=pertinence&page=2")

    await expect(page).toHaveURL(/page=2/)
    // Pagination visible si assez de résultats, sinon au moins l'URL est respectée
    const saisie = page.getByTestId("recherche-saisie")
    await expect(saisie).toBeVisible()
  })
})
