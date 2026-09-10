import { test, expect } from "@playwright/test"

test.describe("Search Workflow", () => {
  test("should search for documents and display results with preview", async ({ page }) => {
    await page.goto("/")

    // Find search input and type query
    const searchInput = page.getByPlaceholder(/Search files, folders/i)
    await expect(searchInput).toBeVisible()

    await searchInput.fill("CLIENT")
    await searchInput.press("Enter")

    // Expect results to be displayed
    const resultsContainer = page.locator("main")
    await expect(resultsContainer).toBeVisible()

    // Wait for at least one result item
    const firstResult = page.locator("article, [data-result-item], li, .group").filter({ hasText: /CLIENT/i }).first()
    await expect(firstResult).toBeVisible({ timeout: 10000 })

    // Click on result to open preview panel
    await firstResult.click()

    // Expect preview panel to display metadata or text
    const previewPanel = page.locator("aside, [aria-label='Preview']")
    await expect(previewPanel).toBeVisible()
  })
})
