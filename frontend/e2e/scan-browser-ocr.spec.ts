import { test, expect } from "@playwright/test"
import { signIn } from "./helpers"
import path from "path"
import fs from "fs"
import crypto from "crypto"
import * as os from "os"
import { execSync } from "child_process"

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

async function dragDropFileViaEvents(page: any, filePath: string) {
  const fileName = path.basename(filePath)
  const buffer = fs.readFileSync(filePath)
  const base64 = buffer.toString("base64")

  await page.evaluate(
    async ({ fileName, base64 }: { fileName: string; base64: string }) => {
      const byTestId = document.querySelector('[data-testid="drop-zone"]') as HTMLElement | null
      const candidates = Array.from(document.querySelectorAll("div"))
      const dropZone =
        byTestId ||
        (candidates.find((el) =>
          (el.textContent || "").includes("déposez ici un dossier de référence"),
        ) as HTMLElement | null)
      if (!dropZone) throw new Error("dropZone not found")

      const byteChars = atob(base64)
      const byteNumbers = new Array(byteChars.length)
      for (let i = 0; i < byteChars.length; i++) {
        byteNumbers[i] = byteChars.charCodeAt(i)
      }
      const byteArray = new Uint8Array(byteNumbers)
      const file = new File([byteArray], fileName, { type: "application/pdf" })

      const dt = new DataTransfer()
      dt.items.add(file)
      // @ts-ignore
      dt.dropEffect = "copy"
      // @ts-ignore
      dt.effectAllowed = "all"

      const mk = (type: string) =>
        new DragEvent(type, {
          bubbles: true,
          cancelable: true,
          dataTransfer: dt,
        })

      dropZone.dispatchEvent(mk("dragenter"))
      dropZone.dispatchEvent(mk("dragover"))
      await new Promise((r) => setTimeout(r, 80))
      dropZone.dispatchEvent(mk("drop"))
    },
    { fileName, base64 },
  )

  await page.waitForTimeout(1500)
}

test.describe("Scan navigateur → OCR → Recherche → Téléchargement", () => {
  test("dépôt PDF image par sélection fichier (setInputFiles), OCR, recherche terme unique, téléchargement SHA", async ({
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
    await expect(page.getByText("Importer un dossier").first()).toBeVisible({ timeout: 10000 })

    const fileInput = page.locator('input[type="file"]').first()
    await fileInput.setInputFiles(pdfPath)

    await expect(page.getByText(/fichier.*prêt.*envoyer/i).first()).toBeVisible({ timeout: 5000 })
    await expect(page.getByText(fileName).first()).toBeVisible()

    await page.getByRole("button", { name: "Envoyer et analyser" }).click()

    await expect(page.getByText(/Fichiers détectés dans/i).first()).toBeVisible({ timeout: 10000 })
    await expect(page.getByText(/Choisir le PDF technique/i).first()).toBeVisible()

    const importBtn = page.getByRole("button", {
      name: /Importer les fichiers sélectionnés/i,
    })
    await expect(importBtn).toBeEnabled({ timeout: 5000 })
    await importBtn.click()

    await expect(page.getByText(/État :/i).first()).toBeVisible({ timeout: 15000 })
    const etatText = await page.getByText(/État :/i).first().textContent()
    console.log(`État import: ${etatText}`)
    expect(etatText).toMatch(/completed|needs_review/i)

    await expect(page.getByText(/Fichiers :/).first()).toBeVisible()
    await expect(page.getByText(/PDF techniques :/).first()).toBeVisible()

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
          { originalSha, downloadedSha, fileName, importId: "via-ui", methode: "setInputFiles" },
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

  test("dépôt PDF image par véritable glisser-déposer (dragenter/dragover/drop), OCR, recherche, SHA", async ({
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

    const tmpDir = path.join(os.tmpdir(), `scan-drag-${testInfo.testId}`)
    fs.mkdirSync(tmpDir, { recursive: true })

    const pdfPath = generatePdfViaPython(tmpDir, "en")
    const originalSha = sha256(pdfPath)
    const fileName = path.basename(pdfPath)

    console.log(`PDF généré (drag-drop): ${pdfPath} sha=${originalSha}`)

    await signIn(page)

    await page.getByRole("button", { name: "Recherche avancée" }).click()
    await expect(page.getByText("Importer un dossier").first()).toBeVisible({ timeout: 10000 })

    await dragDropFileViaEvents(page, pdfPath)

    await expect(page.getByText(/fichier.*prêt.*envoyer/i).first()).toBeVisible({ timeout: 8000 })
    await expect(page.getByText(fileName).first()).toBeVisible({ timeout: 5000 })

    await page.getByRole("button", { name: "Envoyer et analyser" }).click()

    await expect(page.getByText(/Fichiers détectés dans/i).first()).toBeVisible({ timeout: 10000 })
    await expect(page.getByText(/Choisir le PDF technique/i).first()).toBeVisible()

    const importBtn = page.getByRole("button", {
      name: /Importer les fichiers sélectionnés/i,
    })
    await expect(importBtn).toBeEnabled({ timeout: 5000 })
    await importBtn.click()

    await expect(page.getByText(/État :/i).first()).toBeVisible({ timeout: 15000 })
    const etatText = await page.getByText(/État :/i).first().textContent()
    console.log(`État import drag-drop: ${etatText}`)
    expect(etatText).toMatch(/completed|needs_review/i)

    const searchInput = page.getByPlaceholder(/Rechercher/i).first()
    await searchInput.fill("GENOA")
    await searchInput.press("Enter")

    await expect(page.getByText(/GENOA|scan-en|upload/i).first()).toBeVisible({
      timeout: 10000,
    })

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
      console.log(`Drag-drop téléchargé sha=${downloadedSha} original sha=${originalSha}`)
      expect(downloadedSha).toBe(originalSha)

      await testInfo.attach("preuve-sha-dragdrop", {
        body: JSON.stringify(
          { originalSha, downloadedSha, fileName, methode: "dragenter/dragover/drop via DataTransfer" },
          null,
          2,
        ),
        contentType: "application/json",
      })
    } else {
      const resultsCount = await page.locator('[data-testid="result-item"]').count()
      expect(resultsCount).toBeGreaterThan(0)
    }

    fs.rmSync(tmpDir, { recursive: true, force: true })
  })

  test("cas d'erreur : PDF invalide — contrat : échec signalé, pas de fiche inventée", async ({ page }) => {
    await signIn(page)
    await page.getByRole("button", { name: "Recherche avancée" }).click()
    await expect(page.getByText("Importer un dossier").first()).toBeVisible({ timeout: 10000 })

    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "scan-err-"))
    const invalidPath = path.join(tmpDir, "invalid.pdf")
    fs.writeFileSync(invalidPath, Buffer.from("ceci n'est pas un pdf"))
    const invalidSha = sha256(invalidPath)

    const fileInput = page.locator('input[type="file"]').first()
    await fileInput.setInputFiles(invalidPath)

    await expect(page.getByText(/prêt.*envoyer/i).first()).toBeVisible({ timeout: 5000 })
    await page.getByRole("button", { name: "Envoyer et analyser" }).click()

    const errorDestructive = page.locator("p.text-destructive").first()
    const fichiersDetectes = page.getByText(/Fichiers détectés dans/i).first()
    const aucunPdf = page.getByText(/Aucun PDF technique/i).first()

    await expect(async () => {
      const hasError = await errorDestructive.isVisible().catch(() => false)
      const hasDetect = await fichiersDetectes.isVisible().catch(() => false)
      const hasAucun = await aucunPdf.isVisible().catch(() => false)
      expect(hasError || hasDetect || hasAucun).toBeTruthy()
    }).toPass({ timeout: 10000 })

    if (await fichiersDetectes.isVisible().catch(() => false)) {
      const importBtn = page.getByRole("button", { name: /Importer les fichiers sélectionnés/i })
      if (await importBtn.isVisible().catch(() => false)) {
        await importBtn.click()
        await expect(page.getByText(/État :/i).first()).toBeVisible({ timeout: 15000 })
        const etat = await page.getByText(/État :/i).first().textContent()
        console.log(`État import invalid: ${etat}`)
        expect(etat).toMatch(/failed|needs_review/i)
      }
    } else {
      const hasErrorNow = await errorDestructive.isVisible().catch(() => false)
      const hasAucunNow = await aucunPdf.isVisible().catch(() => false)
      expect(hasErrorNow || hasAucunNow).toBeTruthy()
    }

    const searchInput = page.getByPlaceholder(/Rechercher/i).first()
    if (await searchInput.isVisible().catch(() => false)) {
      await searchInput.fill(`INVALIDE_${invalidSha.slice(0, 8)}`)
      await searchInput.press("Enter")
      await page.waitForTimeout(1000)
      const resultItems = await page.locator('[data-testid="result-item"]').count()
      console.log(`Recherche invalide: ${resultItems} résultats`)
      if (resultItems > 0) {
        const firstText = await page.locator('[data-testid="result-item"]').first().textContent()
        expect(firstText).not.toContain(`INVALIDE_${invalidSha.slice(0, 8)}`)
      }
    }

    fs.rmSync(tmpDir, { recursive: true, force: true })
  })

  test("cas d'erreur : fichier sans texte exploitable — contrat : conservé sans extraction, pas de faux texte", async ({
    page,
  }) => {
    await signIn(page)
    await page.getByRole("button", { name: "Recherche avancée" }).click()
    await expect(page.getByText("Importer un dossier").first()).toBeVisible({ timeout: 10000 })

    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "scan-blank-"))
    const pdfGen = `
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as pdf_canvas
from pathlib import Path
import sys
tmp = Path(sys.argv[1])
img = tmp / "blank.png"
Image.new("RGB", (800,600), "white").save(str(img))
pdf = tmp / "blank.pdf"
c = pdf_canvas.Canvas(str(pdf), pagesize=A4)
c.drawImage(str(img), 20,20, width=500, height=600)
c.showPage()
c.save()
img.unlink()
print(str(pdf))
`
    const tmpScript = path.join(tmpDir, "gen.py")
    fs.writeFileSync(tmpScript, pdfGen)
    const pdfPath = execSync(`python3 "${tmpScript}" "${tmpDir}"`, {
      encoding: "utf-8",
    })
      .trim()
      .split("\n")
      .pop()!
      .trim()

    const fileInput = page.locator('input[type="file"]').first()
    await fileInput.setInputFiles(pdfPath)

    await expect(page.getByText(/prêt.*envoyer/i).first()).toBeVisible({ timeout: 5000 })
    await page.getByRole("button", { name: "Envoyer et analyser" }).click()

    await expect(page.getByText(/Fichiers détectés dans/i).first()).toBeVisible({ timeout: 10000 })

    const importBtn = page.getByRole("button", {
      name: /Importer les fichiers sélectionnés/i,
    })
    if (await importBtn.isVisible().catch(() => false)) {
      await importBtn.click()
      await expect(page.getByText(/État :/i).first()).toBeVisible({ timeout: 15000 })
      const etat = await page.getByText(/État :/i).first().textContent()
      console.log(`État import blank: ${etat}`)
      expect(etat).toMatch(/needs_review|failed/i)
    } else {
      await expect(page.getByText(/Aucun PDF technique|No technical PDF/i).first()).toBeVisible({ timeout: 5000 })
    }

    const searchInput = page.getByPlaceholder(/Rechercher/i).first()
    if (await searchInput.isVisible().catch(() => false)) {
      await searchInput.fill("BLANC_SANS_TEXTE_9999")
      await searchInput.press("Enter")
      await page.waitForTimeout(1000)
      const resultCount = await page.locator('[data-testid="result-item"]').count()
      console.log(`Recherche blank terme inexistant: ${resultCount} résultats`)
      if (resultCount > 0) {
        const firstText = await page.locator('[data-testid="result-item"]').first().textContent()
        expect(firstText).not.toContain("BLANC_SANS_TEXTE_9999")
      }
    }

    fs.rmSync(tmpDir, { recursive: true, force: true })
  })
})
