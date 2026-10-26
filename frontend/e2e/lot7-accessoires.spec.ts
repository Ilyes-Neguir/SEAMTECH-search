import { expect, test } from "@playwright/test"
import { APPROBATEUR, LECTURE, signIn, signInWith } from "./helpers"

/**
 * Lot 7 — la famille accessoires dans le navigateur, contre PostgreSQL réel.
 * Le seed dépose un vrai dossier « ACCESSOIRES E2E » (PDF généré) : la fiche
 * LOT7-E2E-1 naît accessoire par le nom du dossier, avec sa projection.
 *
 * Parcours prouvés : onglet famille → file → détail → création du modèle par
 * l'approbateur (liaison unique) → correction historisée ; recherche :
 * facette Catégorie + badge « Accessoire » ; rôle lecture : aucun accès à
 * l'écran de décision. Les gardes d'API vivent dans
 * tests/test_lot7_accessoires.py.
 */

const CODE = "LOT7-E2E-1"
const MODELE = "Prototype E2E Lot7"

test.describe("Lot 7 — accessoires", () => {
  test.skip(
    !process.env.SEAMTECH_E2E_DATABASE_URL,
    "Lot 7 navigateur : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL et comptes nominatifs)",
  )

  test("approbateur : file accessoires, liaison créée, correction historisée", async ({ page }) => {
    await signInWith(page, APPROBATEUR)

    await page.goto("/extractions?famille=accessoires")
    const file = page.getByTestId("file-accessoires")
    await expect(file).toBeVisible()
    const ligne = file.getByTestId("accessoire-item").filter({ hasText: CODE }).first()
    await expect(ligne).toBeVisible({ timeout: 30000 })
    await ligne.click()

    await expect(page.getByTestId("code-accessoire")).toContainText(CODE, { timeout: 20000 })
    await expect(page.getByTestId("panneau-modele-accessoire")).toBeVisible()
    // Valeurs lues par libellés : le type et les cotes canoniques (mm → m).
    await expect(page.getByTestId("champ-accessoire-type_accessoire")).toHaveValue(/coussin/i)
    await expect(page.getByTestId("champ-accessoire-longueur_m")).toHaveValue("1.2")

    // Un seul modèle : l'approbateur le crée et le lie, rien n'est deviné.
    await page.getByTestId("nouveau-modele-accessoire").fill(MODELE)
    await page.getByTestId("bouton-creer-modele-accessoire").click()
    await expect(page.getByTestId("modele-lie-accessoire")).toContainText(MODELE, {
      timeout: 30000,
    })
    await expect(
      page.getByTestId("detail-accessoire").getByTestId("statut-liaison").first(),
    ).toHaveAttribute("data-statut", "lie_humain")

    // Correction historisée avec motif.
    await page.getByTestId("motif-accessoire").fill("Parcours e2e Lot 7 : quantité recomptée.")
    await page.getByTestId("champ-accessoire-quantite").fill("6")
    await page.getByTestId("corriger-accessoire-quantite").click()
    await expect(page.getByTestId("message-accessoire")).toBeVisible({ timeout: 30000 })
    await expect(page.getByTestId("historique-corrections-accessoire")).toContainText("quantite")
    await expect(page.getByTestId("historique-corrections-accessoire")).toContainText("6")
  })

  test("recherche : facette Catégorie et badge Accessoire", async ({ page }) => {
    await signIn(page)
    await page.goto("/recherche")

    const facette = page.getByTestId("facette-categorie")
    await expect(facette).toBeVisible({ timeout: 30000 })
    await facette.getByRole("button", { name: /Accessoires/ }).click()

    const carte = page.locator(`li[data-code="${CODE}"]`).first()
    await expect(carte).toBeVisible({ timeout: 30000 })
    await expect(carte.getByTestId("badge-categorie")).toContainText("Accessoire")
    // Le filtre ne garde que la famille : la fiche voile de référence sort
    // (data-code cible les CARTES de résultat, pas les li imbriqués).
    await expect(page.locator('li[data-code="7792-SO"]')).toHaveCount(0, { timeout: 15000 })
  })

  test("fiche : catégorie relisible avec sa source", async ({ page }) => {
    await signInWith(page, APPROBATEUR)
    await page.goto(`/fiches/${CODE}`)
    const categorie = page.getByTestId("fiche-categorie")
    await expect(categorie).toContainText("Accessoire", { timeout: 30000 })
    await expect(categorie).toContainText("dossier")
    await expect(page.getByTestId("bouton-reclasser")).toBeVisible()
  })

  test("lecture : l'écran de décision reste fermé", async ({ page }) => {
    await signInWith(page, LECTURE)
    await page.goto("/extractions?famille=accessoires")
    await expect(page.getByTestId("file-accessoires")).toHaveCount(0)
    await expect(page.getByText(/réservées à un approbateur/)).toBeVisible({ timeout: 20000 })
  })
})
