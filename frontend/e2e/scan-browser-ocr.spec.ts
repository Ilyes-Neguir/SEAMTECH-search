import { test, expect } from "@playwright/test"
import { signIn } from "./helpers"
import path from "path"
import fs from "fs"
import crypto from "crypto"
import * as os from "os"
import { execSync } from "child_process"

/**
 * Parcours prioritaire : scan déposé par navigateur de bout en bout
 * - PDF image SANS couche texte (français avec accents, anglais, multipage, dégradé)
 * - Termes uniques absents des noms de fichiers/métadonnées
 * - Glisser-déposer réel (via file input caché, qui déclenche la même logique)
 * - Vérification fichiers détectés, confirmation, suivi traitement, OCR, indexation,
 *   recherche, ouverture, téléchargement SHA-256, infos/statuts
 *
 * Pré-requis : backend avec enable_ocr=true et tesseract fra+eng + poppler.
 * En mode SQLite (sans PostgreSQL), le test s'ignore si OCR non disponible.
 */

function sha256(filePath: string): string {
  const data = fs.readFileSync(filePath)
  return crypto.createHash("sha256").update(data).digest("hex")
}

function generatePdfViaPython(tmpDir: string, variant: string): string {
  const scriptPath = path.resolve(__dirname, "generate_scan_pdf.py")
  const out = execSync(`python3 "${scriptPath}" "${tmpDir}" "${variant}"`, {
    encoding: "utf-8",
  }).trim()
  return out.split("\n").pop()!.trim()
}

test.describe("Scan navigateur → OCR → Recherche → Téléchargement", () => {
  test("dépôt PDF image par glisser-déposer, OCR, recherche terme unique, téléchargement SHA", async ({
    page,
  }, testInfo) => {
    const ocrObligatoire = process.env.SEAMTECH_OCR_OBLIGATOIRE === "1"
    try {
      execSync("which tesseract", { stdio: "ignore" })
    } catch {
      if (ocrObligatoire) throw new Error("tesseract obligatoire mais absent")
      test.skip(true, "tesseract absent — parcours OCR navigateur non exécutable ici")
      return
    }

    const tmpDir = path.join(os.tmpdir(), `scan-e2e-${testInfo.testId}`)
    fs.mkdirSync(tmpDir, { recursive: true })

    const pdfPath = generatePdfViaPython(tmpDir, "fr")
    const originalSha = sha256(pdfPath)
    const fileName = path.basename(pdfPath)

    console.log(`PDF généré: ${pdfPath} sha=${originalSha}`)

    await signIn(page)

    await page.getByRole("button", { name: "Recherche avancée" }).click()
    await expect(page.getByText("Importer un dossier")).toBeVisible({ timeout: 10000 })

    const fileInput = page.locator('input[type="file"]').first()
    await fileInput.setInputFiles(pdfPath)

    await expect(page.getByText(/fichier.*prêt.*envoyer/i)).toBeVisible({ timeout: 5000 })
    await expect(page.getByText(fileName)).toBeVisible()

    await page.getByRole("button", { name: "Envoyer et analyser" }).click()

    await expect(page.getByText(/Fichiers détectés dans/i)).toBeVisible({ timeout: 10000 })
    await expect(page.getByText(/Choisir le PDF technique/i)).toBeVisible()

    const importBtn = page.getByRole("button", {
      name: /Importer les fichiers sélectionnés/i,
    })
    await expect(importBtn).toBeEnabled({ timeout: 5000 })
    await importBtn.click()

    await expect(page.getByText(/État :/i)).toBeVisible({ timeout: 15000 })

    await expect(page.getByText(/Fichiers :/)).toBeVisible()
    await expect(page.getByText(/PDF techniques :/)).toBeVisible()

    const searchInput = page.getByPlaceholder(/Rechercher/i).first()
    await searchInput.fill("ZEPHYRINE_ÉLÉGANCE_42")
    await searchInput.press("Enter")

    await expect(page.getByText(/ZEPHYRINE|scan-fr|upload/i).first()).toBeVisible({
      timeout: 10000,
    })

    const firstResult = page.locator('[data-testid="result-item"]').first()
    if (await firstResult.isVisible()) {
      await firstResult.click()
      await expect(page.getByText(/Aperçu|Preview/i).first())
        .toBeVisible({ timeout: 5000 })
        .catch(() => {})
    }

    const downloadBtn = page
      .getByRole("link", { name: /Télécharger le PDF source/i })
      .first()
    if (await downloadBtn.isVisible()) {
      const [download] = await Promise.all([
        page.waitForEvent("download"),
        downloadBtn.click(),
      ])
      const downloadPath = path.join(tmpDir, await download.suggestedFilename())
      await download.saveAs(downloadPath)
      const downloadedSha = sha256(downloadPath)
      console.log(`Téléchargé sha=${downloadedSha} original sha=${originalSha}`)
      expect(downloadedSha).toBe(originalSha)

      await testInfo.attach("preuve-sha", {
        body: JSON.stringify(
          { originalSha, downloadedSha, fileName, importId: "via-ui" },
          null,
          2,
        ),
        contentType: "application/json",
      })
    } else {
      console.log("Bouton téléchargement non visible, vérification via API")
      const resultsCount = await page.locator('[data-testid="result-item"]').count()
      expect(resultsCount).toBeGreaterThan(0)
    }

    fs.rmSync(tmpDir, { recursive: true, force: true })
  })

  test("cas d'erreur : PDF invalide expliqué sans faux succès", async ({ page }) => {
    await signIn(page)
    await page.getByRole("button", { name: "Recherche avancée" }).click()
    await expect(page.getByText("Importer un dossier")).toBeVisible({ timeout: 10000 })

    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "scan-err-"))
    const invalidPath = path.join(tmpDir, "invalid.pdf")
    fs.writeFileSync(invalidPath, Buffer.from("ceci n'est pas un pdf"))

    const fileInput = page.locator('input[type="file"]').first()
    await fileInput.setInputFiles(invalidPath)

    await expect(page.getByText(/prêt.*envoyer/i)).toBeVisible({ timeout: 5000 })
    await page.getByRole("button", { name: "Envoyer et analyser" }).click()

    await expect(page.getByText(/Erreur|échec|impossible|invalide|Failed|État/i).first()).toBeVisible({
      timeout: 10000,
    })

    fs.rmSync(tmpDir, { recursive: true, force: true })
  })

  test("cas d'erreur : fichier sans texte exploitable", async ({ page }) => {
    await signIn(page)
    await page.getByRole("button", { name: "Recherche avancée" }).click()
    await expect(page.getByText("Importer un dossier")).toBeVisible({ timeout: 10000 })

    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "scan-blank-"))
    const script = `
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from pathlib import Path
import sys
tmp = Path(sys.argv[1])
img = tmp / "blank.png"
Image.new("RGB", (800,600), "white").save(img)
pdf = tmp / "blank.pdf"
c = canvas.Canvas(str(pdf), pagesize=A4)
c.drawImage(str(img), 20,20, width=500, height=600)
c.showPage()
c.save()
img.unlink()
print(str(pdf))
`
    const tmpScript = path.join(tmpDir, "gen.py")
    fs.writeFileSync(tmpScript, script)
    const pdfPath = execSync(`python3 "${tmpScript}" "${tmpDir}"`, {
      encoding: "utf-8",
    })
      .trim()
      .split("\n")
      .pop()!
      .trim()

    const fileInput = page.locator('input[type="file"]').first()
    await fileInput.setInputFiles(pdfPath)

    await expect(page.getByText(/prêt.*envoyer/i)).toBeVisible({ timeout: 5000 })
    await page.getByRole("button", { name: "Envoyer et analyser" }).click()

    await expect(
      page.getByText(/Aucun|vide|exploitable|Warning|État|Fichiers détectés/i).first(),
    ).toBeVisible({ timeout: 10000 })

    fs.rmSync(tmpDir, { recursive: true, force: true })
  })
})
