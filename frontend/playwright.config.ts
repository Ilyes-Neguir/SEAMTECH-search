import { defineConfig, devices } from "@playwright/test"
import path from "path"

// The UI is gated behind a session cookie (audit issue #5, Option B), so the
// dev server the tests run against needs sign-in configured. Setting these on
// process.env here guarantees the test process and the spawned dev server agree
// on the password — e2e/helpers.ts reads the same variables.
process.env.SEAMTECH_UI_PASSWORD ??= "e2e-shared-password"
process.env.SEAMTECH_SESSION_SECRET ??= "e2e-session-secret-not-for-production-use"

import { execSync } from "child_process"
import { randomUUID } from "crypto"

// Mode « e2e live » : si SEAMTECH_E2E_DATABASE_URL pointe vers une base
// d'administration PostgreSQL de test, on crée une base e2e neuve (migrations,
// gabarits, trois dossiers d'exemple) et le backend spawné ci-dessous démarre
// dessus — le parcours validation est alors éprouvé de bout en bout en
// conditions réelles (couche métier PostgreSQL, §17.1), et répétable (base
// jetable recréée à chaque exécution, les anciennes e2e_% sont purgées).
// Sans cette variable, la suite tourne sur le SQLite de backend.config.json.
//
// SEAMTECH_E2E_RUN_ID identifie LE RUN : le processus principal l'instancie
// une seule fois et les workers le récupèrent hérité — au sein d'un run,
// tous les rechargements de config réutilisent la même base même si les
// tests l'ont déjà partiellement consommée (sinon le re-seed purgerait la
// base sous les pieds du backend spawné).
let baseLive: string | undefined
if (process.env.SEAMTECH_E2E_DATABASE_URL) {
  process.env.SEAMTECH_E2E_RUN_ID ??= randomUUID()
  const sortie = execSync(`python3 ${path.resolve(__dirname, "e2e/seed-live-pg.py")}`, {
    cwd: path.resolve(__dirname, ".."),
    encoding: "utf-8",
    timeout: 120000,
    stdio: ["pipe", "pipe", "inherit"], // stderr visible : décisions du seed traçées
    env: process.env,
  })
  baseLive = (JSON.parse(sortie.trim().split("\n").pop()!) as { url: string }).url
}

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_TEST_BASE_URL || "http://127.0.0.1:3123",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      command: "python3 -m seamtech_search serve --config frontend/e2e/backend.config.json",
      cwd: path.resolve(__dirname, ".."),
      port: 8123,
      reuseExistingServer: true,
      timeout: 30000,
      env: baseLive
        ? {
            // base e2e PostgreSQL fraîche : le serveur spawné est vivant
            SEAMTECH_DATABASE_URL: baseLive,
            SEAMTECH_ROOT_PATHS: path.resolve(__dirname, "../sample_data"),
          }
        : undefined,
    },
    {
      // mode live : front de PRODUCTION (next start) — next dev (Turbopack)
      // dépasse 1 Go de RSS et se fait tuer par l'OOM sur les machines à 2 Go ;
      // un build préalable (`pnpm build`) est alors requis.
      command: baseLive
        ? "npm run start -- --port 3123"
        : "SEAMTECH_API_URL=http://127.0.0.1:8123 npm run dev -- --port 3123",
      port: 3123,
      reuseExistingServer: true,
      timeout: 30000,
      env: {
        SEAMTECH_API_URL: "http://127.0.0.1:8123",
        SEAMTECH_UI_PASSWORD: process.env.SEAMTECH_UI_PASSWORD,
        SEAMTECH_SESSION_SECRET: process.env.SEAMTECH_SESSION_SECRET,
      },
    },
  ],
})
