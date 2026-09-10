import { test, expect } from "@playwright/test"
import path from "path"

test.describe("Import Workflow", () => {
  const sampleFolder = path.resolve(__dirname, "../../sample_data/CLIENT-123")

  test("should scan folder and confirm import of technical PDF", async ({ page }) => {
    await page.goto("/")

    const input = page.getByPlaceholder(/Server folder path/i)
    await expect(input).toBeVisible()

    await input.fill(sampleFolder)

    // Click Scan folder button
    const scanButton = page.getByRole("button", { name: /Scan folder/i })
    await scanButton.click()

    // Candidates section should appear with technical PDF option
    await expect(page.getByText(/technical PDF/i)).toBeVisible({ timeout: 10000 })
    await expect(page.getByText(/fiche-technique\.pdf/i)).toBeVisible()

    // Click import selected PDF / files
    const importSelectedBtn = page.getByRole("button", { name: /Import (selected PDF|Selected Files)/i })
    await importSelectedBtn.click()

    // Expect progress bar or completed result to appear
    await expect(page.getByText(/Status:/i)).toBeVisible({ timeout: 15000 })
    await expect(page.getByText(/REF-2026-CLIENT123/i)).toBeVisible()
  })

  test("should execute quick import with async job progress", async ({ page }) => {
    await page.goto("/")

    const input = page.getByPlaceholder(/Server folder path/i)
    await expect(input).toBeVisible()

    await input.fill(sampleFolder)

    // Click Quick import
    const quickImportBtn = page.getByRole("button", { name: /Quick import/i })
    await quickImportBtn.click()

    // Wait for import result table to show extracted details
    await expect(page.getByText(/Status:/i)).toBeVisible({ timeout: 15000 })
    await expect(page.getByText(/REF-2026-CLIENT123/i)).toBeVisible()
    await expect(page.getByText(/Grand voile/i)).toBeVisible()
  })
})
