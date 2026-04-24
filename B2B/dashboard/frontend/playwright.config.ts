import { defineConfig, devices } from "@playwright/test"

/**
 * Assumes both backend (FastAPI on :8900) and frontend dev server (Next on
 * :3000) are already running. Run with:
 *   npx playwright test
 *   npx playwright test --ui     # interactive
 *   npx playwright test --headed # watch browser
 */
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,          // flows share DB state, keep serial
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
})
