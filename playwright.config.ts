import { defineConfig, devices } from "@playwright/test";

import { resolveDevServerPort } from "./tests/ui/helpers/dev-server-port";

/**
 * UI tests — run explicitly with `bun run test:ui`.
 * They now run as a separate task in CI, see
 * .github/workflows/ci.yml.
 *
 * The dev server starts automatically unless BASE_URL is set.
 * Point at a deployed preview with:
 *   BASE_URL=https://my-preview.vercel.app bun run test:ui
 *
 * The local dev server listens on 3000 in the primary checkout and on a stable
 * per-worktree port in a linked git worktree, so concurrent runs in different
 * worktrees never reuse each other's server (issue #202). Set PLAYWRIGHT_PORT
 * to pick the port explicitly.
 */

const devServerPort = resolveDevServerPort({
  override: process.env["PLAYWRIGHT_PORT"],
  rootDir: __dirname,
});
const devServerUrl = `http://localhost:${devServerPort}`;
const baseURL = process.env["BASE_URL"] ?? devServerUrl;

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
          command: `bun dev --port ${devServerPort}`,
          url: devServerUrl,
          reuseExistingServer: true,
          timeout: 60_000,
          stdout: "pipe",
          stderr: "pipe",
        },
      }),
});
