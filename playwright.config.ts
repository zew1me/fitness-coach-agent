import { defineConfig, devices } from "@playwright/test";

/**
 * UI tests — run explicitly with `bun run test:ui`.
 * They now run as a separate task in CI, see
 * .github/workflows/ci.yml.
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
  // One retry in CI so a flaky streaming-mock run captures a trace (see `trace`
  // below) for the uploaded playwright-report artifact.
  retries: process.env["CI"] ? 1 : 0,
  workers: 1,
  reporter: process.env["CI"]
    ? [["list"], ["html", { open: "never" }]]
    : "list",
  use: {
    baseURL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    // Sandboxed environments sometimes preinstall a Chromium whose build
    // number differs from the one this @playwright/test version downloads.
    // Point PW_CHROMIUM_PATH at that binary to use it instead.
    ...(process.env["PW_CHROMIUM_PATH"]
      ? { launchOptions: { executablePath: process.env["PW_CHROMIUM_PATH"] } }
      : {}),
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  // Auto-start the dev server when running locally.
  // Skipped when BASE_URL is set (targeting a deployed preview).
  ...(process.env["BASE_URL"]
    ? {}
    : {
        webServer: {
          command: "bun dev",
          url: "http://localhost:3000",
          reuseExistingServer: true,
          timeout: 60_000,
          stdout: "pipe",
          stderr: "pipe",
        },
      }),
});
