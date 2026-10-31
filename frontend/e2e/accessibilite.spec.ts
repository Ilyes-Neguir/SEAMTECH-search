import { test, expect, type Page } from "@playwright/test"
import * as fs from "fs"
import * as path from "path"
import { signInWith, ADMIN, APPROBATEUR } from "./helpers"

/**
 * Audit d'accessibilité automatisé (axe-core, WCAG 2.0/2.1 niveaux A et AA)
 * sur chaque écran de l'application, dans les deux thèmes.
 *
 * Contexte campagne 2 (27/10) : le premier scan a trouvé 48 champs de
 * correction sans nom accessible sur /validation, deux <select> anonymes
 * sur /recherche, un lien logo dont le nom ne contenait pas le libellé
 * visible (Label in Name) sur TOUS les écrans, et trois contrastes sous le
 * seuil AA sur /validation. Ce test fige le zéro-violation atteint après
 * correction : toute régression d'accessibilité casse la CI.
 *
 * Limite assumée : axe ne prouve pas l'usage réel au lecteur d'écran (ordre
 * de lecture, annonces dynamiques) — c'est le plancher mécanique, pas le
 * plafond. Le parcours clavier complet est éprouvé par validation.spec.ts.
 */

const AXE_SOURCE = fs.readFileSync(
  path.resolve(__dirname, "../node_modules/axe-core/axe.min.js"),
  "utf-8",
)

type Violation = { id: string; impact: string | null; noeuds: number }

async function scannerAxe(page: Page): Promise<Violation[]> {
  await page.addScriptTag({ content: AXE_SOURCE })
  return page.evaluate(async () => {
    // @ts-expect-error axe est injecté dans la page par le test
    const resultat = await window.axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] },
    })
    return resultat.violations.map(
      (v: { id: string; impact: string | null; nodes: unknown[] }) => ({
        id: v.id,
        impact: v.impact,
        noeuds: v.nodes.length,
      }),
    )
  })
}

async function verifierEcran(page: Page, chemin: string): Promise<void> {
  await page.goto(chemin)
  await page.waitForTimeout(1000) // laisser les données et animations se poser
  const violations = await scannerAxe(page)
  expect(violations, `${chemin} : ${JSON.stringify(violations, null, 1)}`).toEqual([])
}

test.describe("accessibilité axe-core (WCAG A/AA)", () => {
  test.skip(
    !process.env.SEAMTECH_E2E_DATABASE_URL,
    "e2e live : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL)",
  )

  test("écrans métier en approbateur, thème jour puis nuit", async ({ page }) => {
    await signInWith(page, APPROBATEUR)
    for (const chemin of [
      "/recherche",
      "/dossiers",
      "/fichiers",
      "/nouveau",
      "/validation",
      "/extractions",
      "/comparaison",
      "/confiance",
      "/approvals",
      "/qualite",
      "/notifications",
      "/fiches/0901-MM",
    ]) {
      await verifierEcran(page, chemin)
    }
    // Thème nuit : les contrastes sont un autre jeu de couleurs, on rescane
    // les deux écrans les plus denses après bascule.
    await page.getByTestId("bascule-theme").click()
    await page.waitForTimeout(400)
    for (const chemin of ["/recherche", "/validation"]) {
      await verifierEcran(page, chemin)
    }
  })

  test("connexion et écrans d'administration", async ({ page }) => {
    await verifierEcran(page, "/login")
    await signInWith(page, ADMIN)
    for (const chemin of ["/gabarits", "/comptes"]) {
      await verifierEcran(page, chemin)
    }
  })
})
