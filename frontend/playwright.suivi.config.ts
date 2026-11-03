import { defineConfig, devices } from "@playwright/test"
import { randomUUID } from "crypto"
import os from "os"
import path from "path"

// Un seul manifeste privé par exécution Playwright, transmis aussi aux workers.
process.env.SEAMTECH_E2E_SUIVI_INFO ??= path.join(
  os.tmpdir(), `seamtech-suivi-${randomUUID().replaceAll("-", "")}.json`,
)
const racine = path.resolve(__dirname, "..")
const python = process.env.SEAMTECH_E2E_PYTHON || "python3"
const executable = process.env.SEAMTECH_E2E_CHROMIUM_EXECUTABLE

export default defineConfig({
  testDir: "./e2e-import",
  timeout: 45000,
  expect: { timeout: 10000 },
  retries: 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:13135",
    ...devices["Desktop Chrome"],
    ...(executable ? { launchOptions: { executablePath: executable } } : {}),
  },
  webServer: [
    {
      command: `"${python}" frontend/e2e-import/serveur-suivi.py`,
      cwd: racine,
      url: "http://127.0.0.1:18135/live",
      reuseExistingServer: false,
      timeout: 90000,
      // Sans cette option, Playwright tue le groupe avec SIGKILL et empêche
      // le banc d'effacer sa base PG, son Redis privé et ses fichiers.
      gracefulShutdown: { signal: "SIGTERM", timeout: 15000 },
      env: { PYTHONPATH: racine },
    },
    {
      command: "./node_modules/.bin/next dev --hostname 0.0.0.0 --port 13135",
      cwd: __dirname,
      url: "http://127.0.0.1:13135/login",
      reuseExistingServer: false,
      timeout: 90000,
      gracefulShutdown: { signal: "SIGTERM", timeout: 15000 },
      env: {
        NEXT_TELEMETRY_DISABLED: "1",
        SEAMTECH_UI_PASSWORD: "e2e-shared-password",
        SEAMTECH_SESSION_SECRET: "e2e-session-secret-not-for-production-use",
        SEAMTECH_AUTH_TOKEN: "e2e-suivi-jeton-service",
        SEAMTECH_API_URL: "http://127.0.0.1:18135",
      },
    },
  ],
})
