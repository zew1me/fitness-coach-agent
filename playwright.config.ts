import { defineConfig, devices } from "@playwright/test";

/**
 * UI tests — run explicitly with `bun run test:ui`.
 * NOT included in `bun run check` or CI.
 *
 * The dev server starts automatically unless BASE_URL is set.
 * Point at a deployed preview with:
 *   BASE_URL=https://my-preview.vercel.app bun run test:ui
 */

const baseURL = process.env["BASE_URL"] ?? "http://localhost:3000";

export default defineConfig({
  testDir: "./tests/ui",
  fullyParallel: true,
  forbidOnly: !!process.env["CI"],
  retries: 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  // Auto-start the dev server when running locally.
  // Skipped when BASE_URL is set (targeting a deployed preview).
  webServer: process.env["BASE_URL"]
    ? undefined
    : {
        command: "bun dev",
        url: "http://localhost:3000",
        reuseExistingServer: true,
        timeout: 60_000,
        stdout: "pipe",
        stderr: "pipe",
      },
});
