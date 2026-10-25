import { expect, test } from "@playwright/test"
import { APPROBATEUR, LECTURE, UTILISATEUR, signInWith } from "./helpers"

/**
 * Lot 3b — l'écran approbateur des extractions de voiles, éprouvé dans le
 * navigateur contre PostgreSQL réel (base live semée par seed-live-pg.py :
 * les dépôts 7792-SO / GENOA / E2E-TROIS ont produit de vraies lignes
 * `sail_extractions` via les Lots 3a/3b).
 *
 * Le parcours prouvé : file → détail côte à côte (PDF réel) → correction du
 * libellé de bateau brut → décision de liaison (création d'un modèle
 * canonique + alias appris) → correction d'une cote relue dans l'historique.
 * Les refus de rôle sont éprouvés à l'écran (lecture/utilisateur : ni lien
 * de navigation ni accès), les gardes d'API sont prouvées par
 * tests/test_lot3b_liaison_voile.py.
 */

const CODE_REFERENCE = "7792-SO"
const LIBELLE_TEST = `Bateau Démo Lot3b ${Date.now()}`
const MODELE_TEST = `Modèle Démo Lot3b ${Date.now()}`

test.describe("Lot 3b — extractions de voiles", () => {
  test("approbateur : file, PDF, correction du libellé, liaison décidée, cote corrigée", async ({ page }) => {
    await signInWith(page, APPROBATEUR)

    // Le lien de navigation n'existe que pour l'approbateur nominatif.
    await page.getByRole("link", { name: "Extractions" }).click()
    await page.waitForURL(/\/extractions$/, { timeout: 20000 })

    const file = page.getByTestId("file-extractions")
    await expect(file).toBeVisible()
    await expect(file.getByTestId("extraction-item").first()).toBeVisible({ timeout: 30000 })

    // La résolution en lot ne lie que les preuves exactes et rend un bilan.
    await page.getByTestId("bouton-resoudre-lot").click()
    await expect(page.getByTestId("message-extraction")).toContainText("Résolution en lot", { timeout: 30000 })

    // La fiche de référence 7792-SO a une extraction réelle avec son PDF.
    await page.getByTestId("recherche-extractions").fill(CODE_REFERENCE)
    const ligne = file.getByTestId("extraction-item").first()
    await expect(ligne).toContainText(CODE_REFERENCE, { timeout: 30000 })
    await ligne.click()

    await expect(page.getByTestId("code-extraction")).toContainText(CODE_REFERENCE, { timeout: 20000 })
    await expect(page.getByTestId("panneau-liaison")).toBeVisible()
    const canevas = page.getByTestId("pdf-canvas")
    await expect(canevas).toBeVisible({ timeout: 30000 })
    await expect(page.getByTestId("pdf-page")).toContainText("/", { timeout: 30000 })

    // Correction du libellé brut : la résolution est rouverte, plus rien
    // n'est lié tant qu'une preuve exacte ou une décision ne tranche pas.
    await page.getByTestId("motif-correction").fill("Parcours e2e Lot 3b : libellé relu sur le PDF.")
    await page.getByTestId("champ-modele_bateau_brut").fill(LIBELLE_TEST)
    await page.getByTestId("corriger-modele_bateau_brut").click()
    await expect(page.getByTestId("message-extraction")).toBeVisible({ timeout: 30000 })
    await expect(page.getByTestId("libelle-brut")).toHaveText(LIBELLE_TEST, { timeout: 20000 })
    const panneau = page.getByTestId("panneau-liaison")
    await expect(panneau.getByTestId("statut-liaison")).toHaveAttribute(
      "data-statut",
      /non_resolu|ambigu/,
      { timeout: 20000 },
    )

    // Décision humaine : créer le modèle canonique et lier (alias appris).
    await page.getByTestId("nouveau-modele").fill(MODELE_TEST)
    await page.getByTestId("bouton-creer-lier").click()
    await expect(page.getByTestId("message-extraction")).toContainText("alias appris", { timeout: 30000 })
    await expect(panneau.getByTestId("statut-liaison")).toHaveAttribute("data-statut", "lie_humain", {
      timeout: 20000,
    })
    await expect(page.getByTestId("modele-lie")).toContainText(MODELE_TEST)

    // Correction d'une cote vérifiée sur le PDF : historisée à l'écran.
    await page.getByTestId("champ-sf_m").fill("3.09")
    await page.getByTestId("corriger-sf_m").click()
    await expect(page.getByTestId("message-extraction")).toContainText("Correction enregistrée", {
      timeout: 30000,
    })
    const historique = page.getByTestId("historique-corrections")
    await expect(historique).toContainText("modele_bateau_brut")
    await expect(historique).toContainText("sf_m")
    await expect(historique).toContainText("3.09")
  })

  test("lecture : ni lien de navigation, ni accès à l’écran", async ({ page }) => {
    await signInWith(page, LECTURE)
    await expect(page.getByRole("link", { name: "Extractions" })).toHaveCount(0)
    await page.goto("/extractions")
    await expect(page.getByTestId("acces-refuse")).toBeVisible({ timeout: 20000 })
    await expect(page.getByTestId("file-extractions")).toHaveCount(0)
  })

  test("utilisateur : l’écran de décision lui est refusé aussi", async ({ page }) => {
    await signInWith(page, UTILISATEUR)
    await expect(page.getByRole("link", { name: "Extractions" })).toHaveCount(0)
    await page.goto("/extractions")
    await expect(page.getByTestId("acces-refuse")).toBeVisible({ timeout: 20000 })
    await expect(page.getByTestId("file-extractions")).toHaveCount(0)
  })
})
