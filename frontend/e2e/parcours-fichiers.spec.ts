import { expect, test } from "@playwright/test"
import { readFile } from "node:fs/promises"
import path from "node:path"
import { signIn } from "./helpers"

const CODE_REEL = "7792-SO"

// Ces parcours exigent PostgreSQL seedé par seed-live-pg.py, notamment pour
// vérifier les fiches et les dimensions réelles sans inventer de données.
test.describe("Parcours fichiers dans les vues métier", () => {
  test.skip(!process.env.SEAMTECH_E2E_DATABASE_URL, "parcours live : requiert SEAMTECH_E2E_DATABASE_URL (PostgreSQL)")

  test("Recherche dimensionnelle → fiche et fichiers", async ({ page }) => {
    await signIn(page)
    await page.goto(`/recherche?q=${encodeURIComponent("6,60")}`)
    const ouvrirFiche = page.getByRole("link", { name: `Ouvrir la fiche ${CODE_REEL}` })
    await expect(ouvrirFiche).toBeVisible({ timeout: 15000 })
    await ouvrirFiche.click()
    await expect(page).toHaveURL(new RegExp(`/fiches/${CODE_REEL}$`))
    await expect(page.getByTestId("fiche-code")).toHaveText(CODE_REEL)
    await expect(page.getByTestId("pieces-jointes")).toBeVisible()
    await expect(page.getByTestId("visionneuse-piece")).toBeVisible()
  })

  test("Dossiers → détail → téléchargement par identifiant", async ({ page }) => {
    await signIn(page)
    await page.goto("/dossiers")
    const lien = page.locator(`[data-testid="tableau-fiches"] a[href="/fiches/${CODE_REEL}"]`).first()
    await expect(lien).toBeVisible({ timeout: 15000 })
    await lien.click()
    await expect(page.getByTestId("fiche-code")).toHaveText(CODE_REEL)
    const telecharger = page.getByRole("link", { name: /Télécharger .*\.pdf/i }).first()
    await expect(telecharger).toBeVisible()
    expect(await telecharger.getAttribute("href")).toMatch(/^\/api\/pieces\/\d+\/telecharger$/)
  })

  test("Validation → PDF source servi par id et rendu PDF.js", async ({ page }) => {
    await signIn(page)
    await page.goto("/validation")
    const ligne = page.locator(`li[data-code="${CODE_REEL}"]`)
    await expect(ligne).toBeVisible({ timeout: 15000 })
    await ligne.getByTestId("code-fiche").click()
    await expect(page.getByTestId("pdf-page")).toHaveText(/\d+ \/ \d+/, { timeout: 20000 })
    await expect(page.getByTestId("pdf-canvas")).toBeVisible()
    const lienApercu = page.getByRole("link", { name: "Ouvrir l’aperçu dans un nouvel onglet" })
    await expect(lienApercu).toHaveAttribute("href", /^\/api\/pieces\/\d+\/apercu$/)
  })

  test("/fiches/[code] ouvre directement le PDF et l’historique", async ({ page }) => {
    await signIn(page)
    await page.goto(`/fiches/${CODE_REEL}`)
    await expect(page.getByTestId("fiche-code")).toHaveText(CODE_REEL)
    await expect(page.getByTestId("historique-fiche")).toBeVisible()
    await expect(page.getByTestId("pdf-page")).toHaveText(/\d+ \/ \d+/, { timeout: 20000 })
    await expect(page.getByTestId("pdf-canvas")).toBeVisible()
  })
})

test("Fichiers affiche un PDF et les actions restent sur les routes par id", async ({ page }) => {
  await signIn(page)
  const pdf = await readFile(path.resolve(__dirname, "live-fixtures/CLIENT-E2E-TROIS/fiche-trois.pdf"))
  const bytes = new Uint8Array(pdf)
  await page.route("**/api/pieces?**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      total: 1,
      limit: 50,
      offset: 0,
      has_more: false,
      extensions: [".pdf"],
      dossiers: ["CLIENT-E2E-TROIS"],
      pieces: [{ id: 9027, name: "fiche-trois.pdf", extension: ".pdf", size: bytes.byteLength, kind: "pdf", is_primary_pdf: true, previewable: true, dossier: "CLIENT-E2E-TROIS" }],
    }),
  }))
  await page.route("**/api/pieces/9027/apercu", async (route) => {
    const range = route.request().headers().range
    const headers = {
      "Content-Type": "application/pdf",
      "Content-Disposition": "inline; filename=\"fiche-trois.pdf\"; filename*=UTF-8''fiche-trois.pdf",
      "Accept-Ranges": "bytes",
      "X-Content-Type-Options": "nosniff",
    }
    if (route.request().method() === "HEAD") {
      return route.fulfill({ status: 200, headers: { ...headers, "Content-Length": String(bytes.byteLength) }, body: "" })
    }
    if (range) {
      const match = /^bytes=(\d+)-(\d*)$/.exec(range)
      const start = match ? Number(match[1]) : 0
      const end = Math.min(match?.[2] ? Number(match[2]) : start + 65535, bytes.byteLength - 1)
      return route.fulfill({
        status: 206,
        headers: { ...headers, "Content-Length": String(end - start + 1), "Content-Range": `bytes ${start}-${end}/${bytes.byteLength}` },
        body: Buffer.from(bytes.slice(start, end + 1)),
      })
    }
    return route.fulfill({ status: 200, headers: { ...headers, "Content-Length": String(bytes.byteLength) }, body: Buffer.from(bytes) })
  })
  await page.goto("/fichiers")
  await expect(page.getByTestId("archive-browser")).toBeVisible()
  await expect(page.getByTestId("archive-piece-9027")).toContainText("fiche-trois.pdf")
  await expect(page.getByTestId("pdf-page")).toHaveText("1 / 1", { timeout: 15000 })
  await expect(page.getByTestId("pdf-canvas")).toBeVisible()
  await expect(page.getByRole("link", { name: "Télécharger le fichier" })).toHaveAttribute("href", "/api/pieces/9027/telecharger")
})
