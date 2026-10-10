import { expect, test } from "@playwright/test"
import { signIn } from "./helpers"

/**
 * Constat A09 de l'audit du 2026-10-08 — sur l'écran de validation, `/etat`
 * portait une garde de génération mais `/pieces` n'en avait pas : une réponse
 * en retard pouvait poser le document de la fiche A alors que l'écran affichait
 * déjà la fiche B. Champs, code et révision d'un côté, PDF de l'autre : sur une
 * fiche de fabrication, c'est le pire des mélanges (l'opérateur relit un
 * document qui n'est pas celui dont il valide les valeurs).
 *
 * Le test force la course AU LIEU d'espérer la rencontrer : la réponse
 * `/pieces` de la fiche A est RETARDÉE de 3 s, l'opérateur passe à la fiche B
 * dans cet intervalle, puis la réponse de A arrive. L'écran doit rester sur le
 * document de B — et sa visionneuse doit rendre le PDF de B, pas celui de A.
 *
 * Exige la pile live (PostgreSQL seedé par e2e/seed-live-pg.py) : les fiches
 * réelles et leurs pièces jointes y sont, aucune donnée n'est inventée.
 */
test.describe("Validation — pièces jointes et fiche active (constat A09)", () => {
  test.skip(
    !process.env.SEAMTECH_E2E_DATABASE_URL,
    "e2e live : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL)",
  )

  test("une réponse /pieces en retard ne pose pas le PDF d'une autre fiche", async ({ page }) => {
    await signIn(page)

    // Deux fiches de la file qui ont RÉELLEMENT un PDF à montrer.
    const file = await page.request.get("/api/validation/file")
    expect(file.status()).toBe(200)
    const codes = ((await file.json()) as Array<{ code: string }>).map((r) => r.code)
    expect(codes.length).toBeGreaterThanOrEqual(2)

    const avecPdf: Array<{ code: string; idPdf: number }> = []
    for (const code of codes) {
      const reponse = await page.request.get(`/api/fiches/${encodeURIComponent(code)}/pieces`)
      if (reponse.status() !== 200) continue
      const corps = (await reponse.json()) as { pieces: Array<{ id: number; kind: string; is_primary_pdf: boolean }> }
      const piece =
        corps.pieces.find((p) => p.is_primary_pdf) ?? corps.pieces.find((p) => p.kind === "pdf")
      // DEUX DOCUMENTS DISTINCTS sont indispensables : avec un seul PDF rattaché
      // aux deux fiches, le mélange serait invisible et le test ne prouverait rien.
      if (piece && !avecPdf.some((f) => f.idPdf === piece.id)) avecPdf.push({ code, idPdf: piece.id })
      if (avecPdf.length === 2) break
    }
    expect(
      avecPdf.length,
      "deux fiches portant des documents distincts sont nécessaires pour provoquer la course",
    ).toBe(2)
    const [ficheA, ficheB] = avecPdf

    // La réponse de A est servie avec 3 s de retard, la réponse de B passe.
    await page.route(`**/api/fiches/${ficheA.code}/pieces`, async (route) => {
      await new Promise((r) => setTimeout(r, 3000))
      await route.continue()
    })

    await page.goto("/validation")
    const ligneA = page.locator(`li[data-code="${ficheA.code}"]`)
    const ligneB = page.locator(`li[data-code="${ficheB.code}"]`)
    await expect(ligneA).toBeVisible({ timeout: 20000 })
    await expect(ligneB).toBeVisible({ timeout: 20000 })

    // Afficher A (sa réponse /pieces part en retard)…
    await ligneA.getByTestId("code-fiche").click()
    // …puis B immédiatement : l'écran est sur B quand la réponse de A arrive.
    await ligneB.getByTestId("code-fiche").click()
    await expect(page.getByTestId("validation-app")).toHaveAttribute("data-fiche", ficheB.code)
    await expect(page.getByTestId("validation-app")).toHaveAttribute("data-revision", /\d+/, {
      timeout: 20000,
    })

    // Le document affiché appartient à B — et il est rendu (PDF.js), pas vide.
    const telecharger = page.getByRole("link", { name: "Télécharger le fichier" })
    await expect(telecharger).toHaveAttribute("href", `/api/pieces/${ficheB.idPdf}/telecharger`, {
      timeout: 20000,
    })
    await expect(page.getByTestId("pdf-page")).toHaveText(/\d+ \/ \d+/, { timeout: 20000 })
    await expect(page.getByTestId("pdf-canvas")).toBeVisible()

    // Laisse la réponse retardée de A arriver : l'écran ne doit pas changer.
    await page.waitForTimeout(3500)
    await expect(page.getByTestId("validation-app")).toHaveAttribute("data-fiche", ficheB.code)
    await expect(telecharger).toHaveAttribute("href", `/api/pieces/${ficheB.idPdf}/telecharger`)
    await expect(page.getByTestId("pdf-page")).toHaveText(/\d+ \/ \d+/)
  })
})
