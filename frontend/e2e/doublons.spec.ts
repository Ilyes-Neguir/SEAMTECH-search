import { expect, test } from "@playwright/test"
import { signIn } from "./helpers"

/**
 * Lot L.1 — le bandeau de doublon, vérifié À L'ÉCRAN (correctif C.2).
 *
 * Le job e2e était vert sans rien prouver du bandeau : aucune spec ne
 * l'observait. Ce fichier comble le trou, en conditions réelles :
 *
 *   * la base live (e2e/seed-live-pg.py) dépose les trois dossiers habituels
 *     PUIS ajoute la fiche 7792-SO-BIS, deuxième fiche rattachée au MÊME PDF
 *     (même chemin, même empreinte SHA-256), et lance le scan propositif —
 *     le lien exact existe donc vraiment en base ;
 *   * le bandeau doit être VISIBLE avant toute action de l'opérateur : c'est
 *     tout l'intérêt du lot, voir le doublon AVANT de valider ;
 *   * aucune commande de fusion ne doit exister : la décision reste humaine.
 *
 * Sans SEAMTECH_E2E_DATABASE_URL la suite tourne sur SQLite, où le schéma
 * métier n'existe pas : la spec s'ignore (comme validation.spec.ts), et la CI
 * la fait tourner en mode live — un test qui s'ignorerait silencieusement en CI
 * serait signalé par le garde-fou de comptage de l'étape.
 */

const CODE_DOUBLON = "7792-SO-BIS"
const CODE_ORIGINAL = "7792-SO"

test.describe("bandeau de doublon avant validation", () => {
  test.skip(!process.env.SEAMTECH_E2E_DATABASE_URL, "e2e live : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL)")

  test("le doublon exact est visible AVANT toute validation", async ({ page }) => {
    await signIn(page)
    await page.goto("/validation")

    const file = page.getByTestId("file-validation")
    await expect(file).toBeVisible()
    await file.getByTestId("code-fiche").first().waitFor({ timeout: 10000 })

    // La fiche bis est dans la file : elle n'a jamais été validée par ce test.
    const ligne = file.locator("li", { hasText: CODE_DOUBLON })
    await expect(ligne).toBeVisible({ timeout: 10000 })

    // Le bandeau est là AVANT tout clic, et il nomme l'AUTRE fiche.
    const bandeau = ligne.getByTestId("bandeau-doublon")
    await expect(bandeau).toBeVisible()
    await expect(bandeau).toContainText("Doublon exact de")
    await expect(bandeau).toContainText(CODE_ORIGINAL)
    await expect(bandeau).toContainText("empreinte SHA-256 identique")

    // Ouvrir la fiche ne change rien : le bandeau est aussi dans son entête.
    await ligne.getByTestId("code-fiche").click()
    await expect(page.getByTestId("titre-fiche")).toHaveText(CODE_DOUBLON)
    await expect(page.getByTestId("bandeau-doublon").first()).toContainText("Doublon exact de")
  })

  test("aucune commande de fusion n'est proposée à l'opérateur", async ({ page }) => {
    await signIn(page)
    await page.goto("/validation")
    await expect(page.getByTestId("file-validation")).toBeVisible()

    // Assertion NÉGATIVE : le jour où quelqu'un ajoute un bouton de fusion
    // automatique, ce test tombe — c'est exactement ce qu'on veut (RG3 : aucune
    // écriture « valide » sans décision explicite ; aucune fusion automatique).
    await expect(page.getByRole("button", { name: /fusionner|fusion/i })).toHaveCount(0)
    await expect(page.getByRole("link", { name: /fusionner|fusion/i })).toHaveCount(0)

    // Le bandeau lui-même dit que rien n'est fusionné, et ne contient aucun
    // contrôle cliquable autre que la navigation de la file.
    const bandeau = page.getByTestId("bandeau-doublon").first()
    await expect(bandeau).toContainText("aucune fusion automatique")
    await expect(bandeau.locator("button")).toHaveCount(0)
  })
})
