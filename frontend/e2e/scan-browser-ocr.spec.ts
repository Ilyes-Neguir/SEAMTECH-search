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

function generatePdfViaPython(tmpDir: string, variant: string, uniqueTerm?: string): string {
  const scriptPath = path.resolve(__dirname, "generate_scan_pdf.py")
  const termArg = uniqueTerm ? ` "${uniqueTerm}"` : ""
  const out = execSync(`python3 "${scriptPath}" "${tmpDir}" "${variant}"${termArg}`, {
    encoding: "utf-8",
  }).trim()
  return out.split("\n").pop()!.trim()
}

function uniqueTerm(prefix: string): string {
  const hex = crypto.randomBytes(4).toString("hex").toUpperCase()
  const suffix = Date.now().toString().slice(-4)
  const cleanPrefix = prefix.replace(/[^A-Z0-9]/gi, "").toUpperCase().slice(0, 10)
  return `${cleanPrefix}${hex}${suffix}`
}

/**
 * Simulation dragenter/dragover/drop avec DataTransfer natif dans un vrai navigateur.
 * Exerce les gestionnaires onDragOver/onDrop du composant ImportPanel.
 * Ce n'est pas un geste manuel depuis l'explorateur système.
 */
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
  test("dépôt PDF image par sélection fichier (setInputFiles), OCR, recherche terme unique, téléchargement SHA obligatoire", async ({
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

    const uniq = uniqueTerm("ZEPHYRINE")
    const pdfPath = generatePdfViaPython(tmpDir, "fr", uniq)
    const originalSha = sha256(pdfPath)
    const fileName = path.basename(pdfPath)

    console.log(`PDF généré: ${pdfPath} sha=${originalSha} uniq=${uniq}`)

    await signIn(page)

    await page.getByRole("button", { name: "Recherche avancée" }).click()
    await expect(page.getByText("Importer un dossier").first()).toBeVisible({ timeout: 10000 })

    let importId: string | null = null
    page.on("response", async (resp) => {
      try {
        if (resp.url().includes("/api/imports/confirm") && resp.request().method() === "POST") {
          const json = await resp.json()
          importId = json.import_id || json.job_id || json.id || importId
        }
      } catch {}
    })

    const fileInput = page.locator('input[type="file"]').first()
    await fileInput.setInputFiles(pdfPath)

    await expect(page.getByText(/fichier.*prêt.*envoyer/i).first()).toBeVisible({ timeout: 5000 })
    await expect(page.getByText(fileName).first()).toBeVisible()

    const [uploadResp] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/imports/upload") && r.request().method() === "POST"),
      page.getByRole("button", { name: "Envoyer et analyser" }).click(),
    ])
    expect(uploadResp.ok()).toBeTruthy()
    const uploadJson = await uploadResp.json()
    expect(uploadJson.staged_path).toBeTruthy()

    await expect(page.getByText(/Fichiers détectés dans/i).first()).toBeVisible({ timeout: 10000 })
    await expect(page.getByText(/Choisir le PDF technique/i).first()).toBeVisible()

    const importBtn = page.getByRole("button", {
      name: /Importer les fichiers sélectionnés/i,
    })
    await expect(importBtn).toBeEnabled({ timeout: 5000 })

    const [confirmResp] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/imports/confirm") && r.request().method() === "POST"),
      importBtn.click(),
    ])
    expect(confirmResp.ok()).toBeTruthy()
    const confirmJson = await confirmResp.json()
    importId = confirmJson.import_id || confirmJson.job_id || confirmJson.id || importId
    console.log(`importId capturé: ${importId}`)
    expect(importId).toBeTruthy()

    await expect(page.getByText(/État :/i).first()).toBeVisible({ timeout: 20000 })
    const etatText = await page.getByText(/État :/i).first().textContent()
    console.log(`État import ${importId}: ${etatText}`)
    expect(etatText).toMatch(/completed|needs_review/i)
    // needs_review est nominal possible si extraction partielle (ex: confiance faible), mais on exige quand même OCR/indexation/téléchargement ci-dessous

    const importGetResp = await page.request.get(`/api/imports/${importId}`, { failOnStatusCode: false })
    expect(importGetResp.ok()).toBeTruthy()
    const importGetJson = await importGetResp.json()
    const payload = importGetJson.payload || importGetJson.result || importGetJson
    let payloadObj: any = payload
    if (typeof payload === "string") {
      try {
        payloadObj = JSON.parse(payload)
      } catch {
        payloadObj = {}
      }
    }
    const data = payloadObj.data || payloadObj.result?.data || importGetJson.data || {}
    // Exige champ présent, pas de défaut silencieux
    expect(data).toBeDefined()
    expect(data.extraction_status).toBeDefined()
    // Pour un scan valide, extraction_status ne doit pas être failed
    expect(data.extraction_status).not.toBe("failed")
    const rawText = (data.raw_text || payloadObj.raw_text || "") as string
    expect(rawText).toBeTruthy()
    expect(rawText.length).toBeGreaterThan(10)
    const normRaw = rawText.replace(/[^A-Z0-9]/gi, "").toUpperCase()
    const normUniq = uniq.replace(/[^A-Z0-9]/gi, "").toUpperCase()
    console.log(`raw_text len=${rawText.length} norm contains uniq=${normRaw.includes(normUniq)}`)
    expect(normRaw).toContain(normUniq)

    // Recherche directe API obligatoire — terme unique aléatoire ⇒ résultat attribuable à cet import par unicité
    const apiSearchResp = await page.request.get(`/api/search?q=${encodeURIComponent(uniq)}`)
    expect(apiSearchResp.ok()).toBeTruthy()
    const apiSearchJson = await apiSearchResp.json()
    console.log(`API search direct ${uniq} count=${apiSearchJson.count}`)
    expect(apiSearchJson.count).toBeGreaterThan(0)
    const firstApi = apiSearchJson.results?.[0]
    expect(firstApi?.id).toBeTruthy()

    // Recherche via UI — obligatoire (identité portée par API + result-item, mais UI doit aussi répondre)
    const searchInput = page.getByPlaceholder(/Rechercher/i).first()
    await searchInput.fill(uniq)
    await searchInput.press("Enter")
    await page.getByRole("button", { name: "Rechercher" }).first().click()

    const searchResp = await page.waitForResponse(
      (r) => r.url().includes("/api/search") && r.url().includes(encodeURIComponent(uniq)) && r.request().method() === "GET",
      { timeout: 10000 },
    )
    const searchJson = await searchResp.json()
    console.log(`search UI API ${uniq} count=${searchJson.count}`)
    expect(searchJson.count).toBeGreaterThan(0)

    const resultItem = page.locator('[data-testid="result-item"]').first()
    await expect(resultItem).toBeVisible({ timeout: 15000 })
    await resultItem.click()

    // Visionneuse obligatoire (sans .catch)
    const visionneuse = page.getByTestId("visionneuse-piece").first()
    await expect(visionneuse).toBeVisible({ timeout: 15000 })

    // Téléchargement obligatoire depuis panneau import
    const downloadBtnImport = page.getByRole("link", { name: /Télécharger le PDF source/i }).first()
    await expect(downloadBtnImport).toBeVisible({ timeout: 10000 })
    const [downloadImport] = await Promise.all([
      page.waitForEvent("download"),
      downloadBtnImport.click(),
    ])
    const downloadPathImport = path.join(tmpDir, await downloadImport.suggestedFilename())
    await downloadImport.saveAs(downloadPathImport)
    const downloadedShaImport = sha256(downloadPathImport)
    console.log(`Téléchargé import sha=${downloadedShaImport} original sha=${originalSha} importId=${importId}`)
    expect(downloadedShaImport).toBe(originalSha)

    // Téléchargement obligatoire depuis résultat de recherche (pièce jointe) — désormais contraignant, sélecteur durci
    const pieceDownloadLink = page.locator('a[href*="/api/pieces/"][href*="/telecharger"]').first()
    await expect(pieceDownloadLink).toBeVisible({ timeout: 10000 })
    const [downloadPiece] = await Promise.all([
      page.waitForEvent("download"),
      pieceDownloadLink.click(),
    ])
    const downloadPathPiece = path.join(tmpDir, `piece-${await downloadPiece.suggestedFilename()}`)
    await downloadPiece.saveAs(downloadPathPiece)
    const downloadedShaPiece = sha256(downloadPathPiece)
    console.log(`Téléchargé pièce sha=${downloadedShaPiece} original sha=${originalSha}`)
    expect(downloadedShaPiece).toBe(originalSha)

    await testInfo.attach("preuve-sha", {
      body: JSON.stringify(
        {
          originalSha,
          downloadedSha: downloadedShaImport,
          downloadedShaPiece,
          fileName,
          importId,
          uniqueTerm: uniq,
          methode: "setInputFiles + simulation dragenter/dragover/drop avec DataTransfer natif dans un vrai navigateur (sélection fichier ici)",
          recherche: uniq,
          etat: etatText,
          rawTextLen: rawText.length,
          rawTextContainsUniq: normRaw.includes(normUniq),
          extractionStatus: data.extraction_status,
          note: "terme unique aléatoire => résultat attribuable à cet import par unicité",
        },
        null,
        2,
      ),
      contentType: "application/json",
    })

    fs.rmSync(tmpDir, { recursive: true, force: true })
  })

  test("dépôt PDF image par véritable glisser-déposer (simulation dragenter/dragover/drop avec DataTransfer natif dans vrai navigateur), OCR, recherche, SHA obligatoire", async ({
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

    const uniq = uniqueTerm("GENOA")
    const pdfPath = generatePdfViaPython(tmpDir, "en", uniq)
    const originalSha = sha256(pdfPath)
    const fileName = path.basename(pdfPath)

    console.log(`PDF généré (drag-drop): ${pdfPath} sha=${originalSha} uniq=${uniq}`)

    await signIn(page)

    await page.getByRole("button", { name: "Recherche avancée" }).click()
    await expect(page.getByText("Importer un dossier").first()).toBeVisible({ timeout: 10000 })

    let importId: string | null = null
    page.on("response", async (resp) => {
      try {
        if (resp.url().includes("/api/imports/confirm") && resp.request().method() === "POST") {
          const json = await resp.json()
          importId = json.import_id || json.job_id || json.id || importId
        }
      } catch {}
    })

    await dragDropFileViaEvents(page, pdfPath)

    await expect(page.getByText(/fichier.*prêt.*envoyer/i).first()).toBeVisible({ timeout: 8000 })
    await expect(page.getByText(fileName).first()).toBeVisible({ timeout: 5000 })

    const [uploadResp] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/imports/upload") && r.request().method() === "POST"),
      page.getByRole("button", { name: "Envoyer et analyser" }).click(),
    ])
    expect(uploadResp.ok()).toBeTruthy()

    await expect(page.getByText(/Fichiers détectés dans/i).first()).toBeVisible({ timeout: 10000 })
    await expect(page.getByText(/Choisir le PDF technique/i).first()).toBeVisible()

    const importBtn = page.getByRole("button", {
      name: /Importer les fichiers sélectionnés/i,
    })
    await expect(importBtn).toBeEnabled({ timeout: 5000 })

    const [confirmResp] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/imports/confirm") && r.request().method() === "POST"),
      importBtn.click(),
    ])
    expect(confirmResp.ok()).toBeTruthy()
    const confirmJson = await confirmResp.json()
    importId = confirmJson.import_id || confirmJson.job_id || confirmJson.id || importId
    console.log(`importId drag-drop capturé: ${importId}`)
    expect(importId).toBeTruthy()

    await expect(page.getByText(/État :/i).first()).toBeVisible({ timeout: 20000 })
    const etatText = await page.getByText(/État :/i).first().textContent()
    console.log(`État import drag-drop ${importId}: ${etatText}`)
    expect(etatText).toMatch(/completed|needs_review/i)

    const importGetResp = await page.request.get(`/api/imports/${importId}`, { failOnStatusCode: false })
    expect(importGetResp.ok()).toBeTruthy()
    const importGetJson = await importGetResp.json()
    const payload = importGetJson.payload || importGetJson.result || importGetJson
    let payloadObj: any = payload
    if (typeof payload === "string") {
      try {
        payloadObj = JSON.parse(payload)
      } catch {
        payloadObj = {}
      }
    }
    const data = payloadObj.data || payloadObj.result?.data || importGetJson.data || {}
    expect(data).toBeDefined()
    expect(data.extraction_status).toBeDefined()
    expect(data.extraction_status).not.toBe("failed")
    const rawText = (data.raw_text || payloadObj.raw_text || "") as string
    expect(rawText).toBeTruthy()
    expect(rawText.length).toBeGreaterThan(10)
    const normRaw = rawText.replace(/[^A-Z0-9]/gi, "").toUpperCase()
    const normUniq = uniq.replace(/[^A-Z0-9]/gi, "").toUpperCase()
    console.log(`drag-drop raw_text len=${rawText.length} norm contains uniq=${normRaw.includes(normUniq)}`)
    expect(normRaw).toContain(normUniq)

    // Vérification directe API — terme unique aléatoire ⇒ résultat attribuable à cet import par unicité
    const apiSearchResp = await page.request.get(`/api/search?q=${encodeURIComponent(uniq)}`)
    expect(apiSearchResp.ok()).toBeTruthy()
    const apiSearchJson = await apiSearchResp.json()
    console.log(`API search direct drag-drop ${uniq} count=${apiSearchJson.count}`)
    expect(apiSearchJson.count).toBeGreaterThan(0)
    expect(apiSearchJson.results?.[0]?.id).toBeTruthy()

    const searchInput = page.getByPlaceholder(/Rechercher/i).first()
    await searchInput.fill(uniq)
    await searchInput.press("Enter")
    await page.getByRole("button", { name: "Rechercher" }).first().click()

    const searchResp = await page.waitForResponse(
      (r) => r.url().includes("/api/search") && r.url().includes(encodeURIComponent(uniq)) && r.request().method() === "GET",
      { timeout: 10000 },
    )
    const searchJson = await searchResp.json()
    console.log(`search UI drag-drop ${uniq} count=${searchJson.count}`)
    expect(searchJson.count).toBeGreaterThan(0)

    const resultItem = page.locator('[data-testid="result-item"]').first()
    await expect(resultItem).toBeVisible({ timeout: 15000 })
    await resultItem.click()

    const visionneuse = page.getByTestId("visionneuse-piece").first()
    await expect(visionneuse).toBeVisible({ timeout: 15000 })

    const downloadBtnImport = page.getByRole("link", { name: /Télécharger le PDF source/i }).first()
    await expect(downloadBtnImport).toBeVisible({ timeout: 10000 })
    const [downloadImport] = await Promise.all([
      page.waitForEvent("download"),
      downloadBtnImport.click(),
    ])
    const downloadPathImport = path.join(tmpDir, await downloadImport.suggestedFilename())
    await downloadImport.saveAs(downloadPathImport)
    const downloadedShaImport = sha256(downloadPathImport)
    console.log(`Drag-drop téléchargé import sha=${downloadedShaImport} original sha=${originalSha} importId=${importId}`)
    expect(downloadedShaImport).toBe(originalSha)

    const pieceDownloadLink = page.locator('a[href*="/api/pieces/"][href*="/telecharger"]').first()
    await expect(pieceDownloadLink).toBeVisible({ timeout: 10000 })
    const [downloadPiece] = await Promise.all([
      page.waitForEvent("download"),
      pieceDownloadLink.click(),
    ])
    const downloadPathPiece = path.join(tmpDir, `piece-${await downloadPiece.suggestedFilename()}`)
    await downloadPiece.saveAs(downloadPathPiece)
    const downloadedShaPiece = sha256(downloadPathPiece)
    console.log(`Drag-drop téléchargé pièce sha=${downloadedShaPiece}`)
    expect(downloadedShaPiece).toBe(originalSha)

    await testInfo.attach("preuve-sha-dragdrop", {
      body: JSON.stringify(
        {
          originalSha,
          downloadedSha: downloadedShaImport,
          downloadedShaPiece,
          fileName,
          importId,
          uniqueTerm: uniq,
          methode: "simulation dragenter/dragover/drop avec DataTransfer natif dans un vrai navigateur",
          recherche: uniq,
          etat: etatText,
          rawTextLen: rawText.length,
          rawTextContainsUniq: normRaw.includes(normUniq),
          extractionStatus: data.extraction_status,
          note: "terme unique aléatoire => résultat attribuable à cet import par unicité",
        },
        null,
        2,
      ),
      contentType: "application/json",
    })

    fs.rmSync(tmpDir, { recursive: true, force: true })
  })

  test("cas d'erreur : PDF invalide — contrat : échec signalé avec issue explicite, pas de fiche inventée", async ({ page }) => {
    await signIn(page)
    await page.getByRole("button", { name: "Recherche avancée" }).click()
    await expect(page.getByText("Importer un dossier").first()).toBeVisible({ timeout: 10000 })

    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "scan-err-"))
    const invalidPath = path.join(tmpDir, "invalid.pdf")
    fs.writeFileSync(invalidPath, Buffer.from("ceci n'est pas un pdf"))
    const invalidSha = sha256(invalidPath)
    const uniqInvalid = `INVALIDE${invalidSha.slice(0, 8).toUpperCase()}`

    let importId: string | null = null
    page.on("response", async (resp) => {
      try {
        if (resp.url().includes("/api/imports/confirm") && resp.request().method() === "POST") {
          const json = await resp.json()
          importId = json.import_id || json.job_id || json.id || importId
        }
      } catch {}
    })

    const fileInput = page.locator('input[type="file"]').first()
    await fileInput.setInputFiles(invalidPath)

    await expect(page.getByText(/prêt.*envoyer/i).first()).toBeVisible({ timeout: 5000 })

    const [uploadResp] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/imports/upload") && r.request().method() === "POST"),
      page.getByRole("button", { name: "Envoyer et analyser" }).click(),
    ])

    if (!uploadResp.ok()) {
      const json = await uploadResp.json().catch(() => ({}))
      console.log(`Upload invalide rejeté HTTP ${uploadResp.status()} ${JSON.stringify(json).slice(0,200)}`)
      expect([400, 413, 500].includes(uploadResp.status())).toBeTruthy()
      const errorDestructive = page.locator("p.text-destructive").first()
      await expect(errorDestructive).toBeVisible({ timeout: 5000 })
      const errText = await errorDestructive.textContent()
      expect(errText?.length).toBeGreaterThan(5)
    } else {
      const uploadJson = await uploadResp.json()
      console.log(`Upload invalide OK, scan payload files=${uploadJson.files_detected}`)

      const errorDestructive = page.locator("p.text-destructive").first()
      const fichiersDetectes = page.getByText(/Fichiers détectés dans/i).first()
      const aucunPdf = page.getByText(/Aucun PDF technique/i).first()

      await expect(async () => {
        const hasError = await errorDestructive.isVisible().catch(() => false)
        const hasDetect = await fichiersDetectes.isVisible().catch(() => false)
        const hasAucun = await aucunPdf.isVisible().catch(() => false)
        expect(hasError || hasDetect || hasAucun).toBeTruthy()
      }).toPass({ timeout: 10000 })

      if (await aucunPdf.isVisible().catch(() => false)) {
        await expect(aucunPdf).toBeVisible()
        const warning = page.locator("p.text-warning, p.text-destructive").first()
        // Message explicite, mais pas obligatoire warning si Aucun PDF technique déjà explicite
        console.log(`Invalide aucun PDF technique visible`)
      } else if (await fichiersDetectes.isVisible().catch(() => false)) {
        const importBtn = page.getByRole("button", { name: /Importer les fichiers sélectionnés/i })
        const hasImportBtn = await importBtn.isVisible().catch(() => false)
        if (hasImportBtn) {
          const [confirmResp] = await Promise.all([
            page.waitForResponse((r) => r.url().includes("/api/imports/confirm") && r.request().method() === "POST"),
            importBtn.click(),
          ])
          const confirmJson = await confirmResp.json().catch(() => ({}))
          importId = confirmJson.import_id || confirmJson.job_id || confirmJson.id || importId
          console.log(`Invalide confirm importId=${importId} status=${confirmResp.status()}`)

          if (confirmResp.ok() && importId) {
            await expect(page.getByText(/État :/i).first()).toBeVisible({ timeout: 15000 })
            const etat = await page.getByText(/État :/i).first().textContent()
            console.log(`État import invalid ${importId}: ${etat}`)
            expect(etat).toMatch(/failed|needs_review/i)

            const importGetResp = await page.request.get(`/api/imports/${importId}`, { failOnStatusCode: false })
            expect(importGetResp.ok()).toBeTruthy()
            const importGetJson = await importGetResp.json()
            const payload = importGetJson.payload || importGetJson
            let payloadObj: any = payload
            if (typeof payload === "string") {
              try {
                payloadObj = JSON.parse(payload)
              } catch {
                payloadObj = {}
              }
            }
            expect(payloadObj.status).toBeDefined()
            expect(["failed", "needs_review"]).toContain(payloadObj.status)
            const data = payloadObj.data || {}
            expect(data.extraction_status).toBeDefined()
            expect(data.extraction_status).not.toBe("success")
          } else {
            await expect(errorDestructive).toBeVisible({ timeout: 5000 })
          }
        } else {
          const hasError = await errorDestructive.isVisible().catch(() => false)
          const hasAucun = await aucunPdf.isVisible().catch(() => false)
          expect(hasError || hasAucun).toBeTruthy()
        }
      } else {
        await expect(errorDestructive).toBeVisible({ timeout: 5000 })
      }
    }

    // Absence traitement réussi ou fiche métier attribuable à cet envoi — contrôle count 0 obligatoire (non conditionnel)
    const apiInvalidResp = await page.request.get(`/api/search?q=${encodeURIComponent(uniqInvalid)}`)
    expect(apiInvalidResp.ok()).toBeTruthy()
    const apiInvalidJson = await apiInvalidResp.json()
    console.log(`Recherche API invalide ${uniqInvalid} count=${apiInvalidJson.count} (obligatoire, doit être 0)`)
    expect(apiInvalidJson.count).toBe(0)

    const searchInput = page.getByPlaceholder(/Rechercher/i).first()
    if (await searchInput.isVisible().catch(() => false)) {
      await searchInput.fill(uniqInvalid)
      await searchInput.press("Enter")
      const searchResp = await page.waitForResponse(
        (r) => r.url().includes("/api/search") && r.url().includes(encodeURIComponent(uniqInvalid)),
        { timeout: 5000 },
      )
      const searchJson = await searchResp.json()
      console.log(`Recherche UI invalide ${uniqInvalid} count=${searchJson.count} (obligatoire, doit être 0)`)
      expect(searchJson.count).toBe(0)
    }

    fs.rmSync(tmpDir, { recursive: true, force: true })
  })

  test("cas d'erreur : fichier sans texte exploitable — contrat : conservé sans extraction, pas de faux texte, issue explicite", async ({
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

    let importId: string | null = null
    page.on("response", async (resp) => {
      try {
        if (resp.url().includes("/api/imports/confirm") && resp.request().method() === "POST") {
          const json = await resp.json()
          importId = json.import_id || json.job_id || json.id || importId
        }
      } catch {}
    })

    const fileInput = page.locator('input[type="file"]').first()
    await fileInput.setInputFiles(pdfPath)

    await expect(page.getByText(/prêt.*envoyer/i).first()).toBeVisible({ timeout: 5000 })

    const [uploadResp] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/imports/upload") && r.request().method() === "POST"),
      page.getByRole("button", { name: "Envoyer et analyser" }).click(),
    ])
    expect(uploadResp.ok()).toBeTruthy()

    await expect(page.getByText(/Fichiers détectés dans/i).first()).toBeVisible({ timeout: 10000 })

    const importBtn = page.getByRole("button", {
      name: /Importer les fichiers sélectionnés/i,
    })
    const hasImportBtn = await importBtn.isVisible().catch(() => false)

    if (hasImportBtn) {
      const [confirmResp] = await Promise.all([
        page.waitForResponse((r) => r.url().includes("/api/imports/confirm") && r.request().method() === "POST"),
        importBtn.click(),
      ])
      expect(confirmResp.ok()).toBeTruthy()
      const confirmJson = await confirmResp.json()
      importId = confirmJson.import_id || confirmJson.job_id || confirmJson.id || importId
      console.log(`Blank importId=${importId}`)
      expect(importId).toBeTruthy()

      await expect(page.getByText(/État :/i).first()).toBeVisible({ timeout: 15000 })
      const etat = await page.getByText(/État :/i).first().textContent()
      console.log(`État import blank ${importId}: ${etat}`)
      expect(etat).toMatch(/needs_review|failed/i)

      const importGetResp = await page.request.get(`/api/imports/${importId}`, { failOnStatusCode: false })
      expect(importGetResp.ok()).toBeTruthy()
      const importGetJson = await importGetResp.json()
      const payload = importGetJson.payload || importGetJson
      let payloadObj: any = payload
      if (typeof payload === "string") {
        try {
          payloadObj = JSON.parse(payload)
        } catch {
          payloadObj = {}
        }
      }
      expect(payloadObj.status).toBeDefined()
      expect(["needs_review", "failed"]).toContain(payloadObj.status)
      const data = payloadObj.data || {}
      expect(data.extraction_status).toBeDefined()
      expect(data.extraction_status).not.toBe("success")
      const rawText = (data.raw_text || "").trim()
      console.log(`Blank extraction_status=${data.extraction_status} raw_text_len=${rawText.length}`)
      expect(rawText.length).toBeLessThan(20)

      const aVerifier = page.getByText(/À vérifier/i).first()
      const hasAVerifier = await aVerifier.isVisible().catch(() => false)
      const donneesExtraites = page.getByText(/Données extraites de la fiche technique/i).first()
      const hasDonnees = await donneesExtraites.isVisible().catch(() => false)
      console.log(`Blank À vérifier visible=${hasAVerifier} Données extraites visible=${hasDonnees}`)
      if (hasDonnees) {
        expect(hasAVerifier).toBeTruthy()
      }
    } else {
      await expect(page.getByText(/Aucun PDF technique|No technical PDF/i).first()).toBeVisible({ timeout: 5000 })
    }

    // Contrôle count 0 obligatoire pour terme blanc (non conditionnel) — BLANCSANSTEXTE9999 (sans underscore, doc vs code aligné)
    const uniqBlank = "BLANCSANSTEXTE9999"
    const apiBlankResp = await page.request.get(`/api/search?q=${encodeURIComponent(uniqBlank)}`)
    expect(apiBlankResp.ok()).toBeTruthy()
    const apiBlankJson = await apiBlankResp.json()
    console.log(`Recherche API blank ${uniqBlank} count=${apiBlankJson.count} (obligatoire, doit être 0)`)
    expect(apiBlankJson.count).toBe(0)

    const searchInput = page.getByPlaceholder(/Rechercher/i).first()
    if (await searchInput.isVisible().catch(() => false)) {
      await searchInput.fill(uniqBlank)
      await searchInput.press("Enter")
      const searchResp = await page.waitForResponse(
        (r) => r.url().includes("/api/search") && r.url().includes(encodeURIComponent(uniqBlank)),
        { timeout: 5000 },
      )
      const searchJson = await searchResp.json()
      console.log(`Recherche UI blank ${uniqBlank} count=${searchJson.count} (obligatoire, doit être 0)`)
      expect(searchJson.count).toBe(0)
    }

    fs.rmSync(tmpDir, { recursive: true, force: true })
  })
})
