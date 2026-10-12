// Configuration Playwright de la recette virtuelle : mêmes specs que le dépôt
// (frontend/e2e), mêmes serveurs, mais navigateur CHROMIUM RÉEL de la recette
// (Chromium 153 extrait de @sparticuz/chromium) au lieu du navigateur Playwright
// non téléchargeable dans ce sandbox (cdn.playwright.dev injoignable).
const path = require("path");
const { defineConfig, devices } = require("/home/user/SEAMTECH-search/frontend/node_modules/.pnpm/playwright@1.63.0/node_modules/playwright/test");
const DEPOT = "/home/user/SEAMTECH-search";
const FRONT = path.join(DEPOT, "frontend");
process.env.SEAMTECH_UI_PASSWORD ??= "e2e-shared-password";
process.env.SEAMTECH_SESSION_SECRET ??= "e2e-session-secret-not-for-production-use";
process.env.SEAMTECH_AUTH_TOKEN ??= "e2e-service-token";
module.exports = defineConfig({
  testDir: path.join(FRONT, "e2e"),
  globalSetup: path.join(FRONT, "e2e", "global-setup.ts"),
  fullyParallel: false, forbidOnly: false, retries: 0, workers: 1,
  reporter: [["list"], ["json", { outputFile: "/home/user/rv/preuves/e2e-rapport.json" }]],
  expect: { timeout: 15000 },
  use: {
    baseURL: "http://127.0.0.1:3123",
    trace: "on-first-retry",
    launchOptions: {
      executablePath: process.env.RECETTE_CHROMIUM || "/home/user/rv/env/chrome/chromium",
      args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--font-render-hinting=none"],
    },
  },
  projects: [{ name: "chromium-reel", use: { ...devices["Desktop Chrome"] } }],
});
