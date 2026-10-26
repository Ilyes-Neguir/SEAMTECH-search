import { expect, test } from "@playwright/test"
import { signIn } from "./helpers"

/**
 * Lot 8 — la refonte visuelle « voilerie » : deux quarts (jour/nuit),
 * bascule persistée, contrastes AA mesurés DANS le navigateur, navigation
 * groupée par domaine. Skin uniquement : aucun parcours métier ne change
 * (les recettes des lots précédents restent la preuve fonctionnelle).
 *
 * Tourne dans la suite par défaut (SQLite) : la page de connexion suffit —
 * le thème est une affaire de navigateur, pas de base.
 */

function luminance(r: number, g: number, b: number): number {
  const f = (c: number) => {
    const s = c / 255
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}

function analyserRgba(texte: string): [number, number, number] {
  const morceaux = texte.match(/[\d.]+/g)?.map(Number) ?? [0, 0, 0]
  return [morceaux[0] ?? 0, morceaux[1] ?? 0, morceaux[2] ?? 0]
}

function contraste(a: [number, number, number], b: [number, number, number]): number {
  const la = luminance(...a)
  const lb = luminance(...b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

test.describe("Lot 8 — thème voilerie (jour/nuit)", () => {
  test("bascule persistée entre quart de jour et quart de nuit", async ({ page }) => {
    await page.goto("/login")
    const bascule = page.getByTestId("bascule-theme")
    await expect(bascule).toBeVisible()

    const etatInitial = await page.evaluate(() =>
      document.documentElement.classList.contains("dark"),
    )
    await bascule.click()
    await expect
      .poll(() => page.evaluate(() => document.documentElement.classList.contains("dark")))
      .toBe(!etatInitial)

    // Le choix survit au rechargement (et il est appliqué avant peinture :
    // la classe est déjà là quand le DOM est prêt).
    await page.reload()
    await expect(page.getByTestId("bascule-theme")).toBeVisible()
    const apresRechargement = await page.evaluate(() =>
      document.documentElement.classList.contains("dark"),
    )
    expect(apresRechargement).toBe(!etatInitial)
    const memorise = await page.evaluate(() => window.localStorage.getItem("seamtech-quart"))
    expect(memorise).toBe(apresRechargement ? "nuit" : "jour")
  })

  test("contrastes AA (≥ 4,5) dans les deux quarts", async ({ page }) => {
    await page.goto("/login")
    for (const quart of ["jour", "nuit"] as const) {
      await page.evaluate((q) => {
        window.localStorage.setItem("seamtech-quart", q)
      }, quart)
      await page.reload()
      await expect(page.getByTestId("bascule-theme")).toBeVisible()

      const mesures = await page.evaluate(() => {
        const corps = getComputedStyle(document.body)
        const carte = document.querySelector("form")
        const styleCarte = carte ? getComputedStyle(carte) : corps
        const bouton = document.querySelector('button[type="submit"]')
        const styleBouton = bouton ? getComputedStyle(bouton) : corps
        return {
          texte: corps.color,
          fond: corps.backgroundColor,
          fondCarte: styleCarte.backgroundColor,
          boutonFond: styleBouton.backgroundColor,
          boutonTexte: styleBouton.color,
        }
      })
      const texte = analyserRgba(mesures.texte)
      expect(contraste(texte, analyserRgba(mesures.fond)), `${quart} texte/fond`).toBeGreaterThanOrEqual(4.5)
      expect(contraste(texte, analyserRgba(mesures.fondCarte)), `${quart} texte/carte`).toBeGreaterThanOrEqual(4.5)
      expect(
        contraste(analyserRgba(mesures.boutonTexte), analyserRgba(mesures.boutonFond)),
        `${quart} bouton`,
      ).toBeGreaterThanOrEqual(4.5)
    }
  })

  test("navigation groupée par domaine", async ({ page }) => {
    await signIn(page)
    const nav = page.getByTestId("nav")
    await expect(nav).toBeVisible()
    for (const groupe of ["explorer", "atelier", "pilotage"]) {
      await expect(page.getByTestId(`nav-groupe-${groupe}`)).toBeVisible()
    }
    // Les liens métier gardent leurs noms (Lot 6) : seule la peau change.
    await expect(nav.getByRole("link", { name: "Recherche" })).toBeVisible()
  })
})
