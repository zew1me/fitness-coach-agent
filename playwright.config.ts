import { defineConfig, devices } from "@playwright/test";

import { resolveDevServer } from "./tests/ui/helpers/dev-server-port";

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
 * worktrees never reuse each other's server (issue #202); a worktree never
 * reuses an already-running server, so a port collision fails loudly. Set
 * PLAYWRIGHT_PORT to pick the port explicitly.
 */

const devServer = resolveDevServer({
  override: process.env["PLAYWRIGHT_PORT"],
  rootDir: __dirname,
});
const devServerUrl = `http://localhost:${devServer.port}`;
const baseURL = process.env["BASE_URL"] ?? devServerUrl;
const TOUCH_SPECS = /.*\.touch\.spec\.ts$/;

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
      testIgnore: TOUCH_SPECS,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // Touch-device specs (issue #553): iPhone-class emulation — mobile
      // viewport/UA, isMobile (meta viewport honored), hasTouch, 3x DPR — run
      // on Chromium because CI installs only Chromium. Kept to `*.touch.spec.ts`
      // so the desktop suite is not run twice.
      name: "mobile-touch",
      testMatch: TOUCH_SPECS,
      use: { ...devices["iPhone 15"], browserName: "chromium" },
    },
  ],
  // Auto-start the dev server when running locally.
  // Skipped when BASE_URL is set (targeting a deployed preview).
  ...(process.env["BASE_URL"]
    ? {}
    : {
        webServer: {
          command: `bun dev --port ${devServer.port}`,
          url: devServerUrl,
          reuseExistingServer: devServer.reuseExistingServer,
          timeout: 60_000,
          stdout: "pipe",
          stderr: "pipe",
        },
      }),
});
