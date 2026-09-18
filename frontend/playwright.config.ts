import { defineConfig, devices } from "@playwright/test"
import path from "path"

// The UI is gated behind a session cookie (audit issue #5, Option B), so the
// dev server the tests run against needs sign-in configured. Setting these on
// process.env here guarantees the test process and the spawned dev server agree
// on the password — e2e/helpers.ts reads the same variables.
process.env.SEAMTECH_UI_PASSWORD ??= "e2e-shared-password"
process.env.SEAMTECH_SESSION_SECRET ??= "e2e-session-secret-not-for-production-use"

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
    },
    {
      command: "SEAMTECH_API_URL=http://127.0.0.1:8123 npm run dev -- --port 3123",
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
