import { defineConfig, devices } from "@playwright/test";

/**
 * UI tests — run explicitly with `bun run test:ui`.
 * NOT included in `bun run check` or CI.
 *
 * Requires the dev server to be running:
 *   bun run dev        (remote Supabase)
 *   bun run dev:local  (local Supabase)
 *
 * Or pass BASE_URL to point at a deployed preview:
 *   BASE_URL=https://my-preview.vercel.app bun run test:ui
 */
export default defineConfig({
  testDir: "./tests/ui",
  fullyParallel: true,
  forbidOnly: !!process.env["CI"],
  retries: 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: process.env["BASE_URL"] ?? "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  // webServer is optional — omitted so tests can also target deployed previews.
  // Start the server manually before running: `bun dev` or `bun run dev:local`
});
