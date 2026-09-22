import { test, expect } from "@playwright/test"
import { signIn } from "./helpers"

test.describe("Search Workflow", () => {
  test("should search for documents and display results with preview", async ({ page }) => {
    await signIn(page)

    // Lot E : la recherche fichiers de la Phase 0 vit sur /fichiers ;
    // /recherche est désormais la recherche hybride des fiches techniques.
    await page.goto("/fichiers")

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

    // Expect preview panel to display metadata or text.
    // Target the panel's complementary landmark: the previous selector
    // ("aside, [aria-label='Preview']") also matched the per-row "Preview"
    // buttons rendered by result-item.tsx, i.e. 6 unrelated elements.
    const previewPanel = page.getByRole("complementary")
    await expect(previewPanel).toBeVisible()
  })
})
