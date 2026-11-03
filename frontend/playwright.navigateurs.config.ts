/**
 * Configuration Playwright de la qualification NAVIGATEURS (Chrome, Edge).
 *
 * Pourquoi une configuration à part : les projets « chrome » et « msedge »
 * utilisent les binaires RÉELS de la machine (``channel``), qui n'existent que
 * sur un poste ou un runner Windows. Les mélanger à la configuration
 * principale casserait la CI Linux, où seul le Chromium de Playwright est
 * installé.
 *
 * Aucune base PostgreSQL, aucune graine : cette qualification porte sur le
 * RENDU et l'ACCESSIBILITÉ de l'interface dans deux navigateurs, pas sur des
 * données. Les écrans sont donc vus dans leur état de lecture.
 */
import { defineConfig, devices } from "@playwright/test"
import path from "path"

process.env.SEAMTECH_UI_PASSWORD ??= "e2e-shared-password"
process.env.SEAMTECH_SESSION_SECRET ??= "e2e-session-secret-not-for-production-use"
process.env.SEAMTECH_AUTH_TOKEN ??= "e2e-service-token"

// Sous Windows, l'interpréteur s'appelle ``python`` : ``python3`` n'existe pas
// hors du monde POSIX (et setup-python ne le fournit pas partout).
const interpreteur = process.platform === "win32" ? "python" : "python3"

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: false,
  retries: 0,
  workers: 1,
  reporter: [["list"], ["json", { outputFile: "navigateurs-resultats.json" }]],
  expect: { timeout: 20000 },
  use: {
    baseURL: process.env.PLAYWRIGHT_TEST_BASE_URL || "http://127.0.0.1:3123",
    trace: "off",
  },
  projects: [
    {
      name: "chrome",
      use: { ...devices["Desktop Chrome"], channel: "chrome" },
    },
    {
      name: "msedge",
      use: { ...devices["Desktop Edge"], channel: "msedge" },
    },
  ],
  webServer: [
    {
      command: `${interpreteur} -m seamtech_search serve --config frontend/e2e/backend.config.json`,
      cwd: path.resolve(__dirname, ".."),
      port: 8123,
      reuseExistingServer: true,
      timeout: 120000,
    },
    {
      // PAS de ``cwd`` : Playwright lance le serveur depuis le dossier de la
      // configuration, c'est-à-dire ``frontend/``, seul endroit où se trouve
      // le package.json de l'interface. Le préciser (racine du dépôt) faisait
      // échouer le démarrage en ENOENT sur Windows (run 41465342706) — la
      // configuration principale ne le précise d'ailleurs pas.
      command: "npm run dev -- --port 3123",
      port: 3123,
      reuseExistingServer: true,
      timeout: 240000,
      env: {
        SEAMTECH_API_URL: "http://127.0.0.1:8123",
        SEAMTECH_AUTH_TOKEN: process.env.SEAMTECH_AUTH_TOKEN,
        SEAMTECH_UI_PASSWORD: process.env.SEAMTECH_UI_PASSWORD,
        SEAMTECH_SESSION_SECRET: process.env.SEAMTECH_SESSION_SECRET,
      },
    },
  ],
})
