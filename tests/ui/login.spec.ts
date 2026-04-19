/**
 * Login page UI tests — covers hash-fragment and query-string error handling
 * added in issue #34 / PR #57.
 *
 * Run with:  bun run test:ui
 * Requires the dev server to be running on http://localhost:3000 (or BASE_URL).
 */
import { test, expect } from "@playwright/test";

// ── helpers ──────────────────────────────────────────────────────────────────

async function emailInput(page: import("@playwright/test").Page) {
  return page.getByLabel("Email");
}

async function sendButton(page: import("@playwright/test").Page) {
  return page.getByRole("button", { name: /send magic link/i });
}

// ── hash-fragment errors (set by Supabase, never visible to server) ───────────

test.describe("hash-fragment error handling", () => {
  test("otp_expired shows friendly message and email form", async ({ page }) => {
    await page.goto(
      "/login?return_to=/#error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired",
    );

    // Email form should be visible (reset to entry mode)
    await expect(await emailInput(page)).toBeVisible();
    await expect(await sendButton(page)).toBeVisible();

    // Friendly message visible
    const errorEl = page.locator("p.error");
    await expect(errorEl).toBeVisible();
    await expect(errorEl).toContainText(/expired/i);

    // Raw internal strings must NOT appear
    await expect(page.locator("body")).not.toContainText("otp_expired");
    await expect(page.locator("body")).not.toContainText("Email link is invalid");
  });

  test("bad_otp shows friendly message", async ({ page }) => {
    await page.goto("/login#error=access_denied&error_code=bad_otp");

    const errorEl = page.locator("p.error");
    await expect(errorEl).toBeVisible();
    await expect(errorEl).toContainText(/not valid/i);
    await expect(page.locator("body")).not.toContainText("bad_otp");
  });

  test("access_denied shows friendly message", async ({ page }) => {
    await page.goto("/login#error=access_denied&error_code=access_denied");

    const errorEl = page.locator("p.error");
    await expect(errorEl).toBeVisible();
    await expect(errorEl).toContainText(/access was denied/i);
  });

  test("email_not_confirmed shows friendly message", async ({ page }) => {
    await page.goto("/login#error_code=email_not_confirmed");

    const errorEl = page.locator("p.error");
    await expect(errorEl).toBeVisible();
    await expect(errorEl).toContainText(/confirm your email/i);
  });

  test("hash is stripped from URL after parsing", async ({ page }) => {
    await page.goto(
      "/login#error=access_denied&error_code=otp_expired&error_description=expired",
    );

    // Wait for the client-side useEffect to fire
    await page.waitForTimeout(300);
    expect(page.url()).not.toContain("#error");
  });
});

// ── query-string errors (set by auth/callback route) ─────────────────────────

test.describe("query-string error handling", () => {
  test("'Missing auth code' maps to friendly message", async ({ page }) => {
    await page.goto("/login?error=Missing+auth+code+from+Supabase.");

    const errorEl = page.locator("p.error");
    await expect(errorEl).toBeVisible();
    // Friendly copy — not the raw internal string
    await expect(errorEl).not.toContainText("Missing auth code from Supabase");
    await expect(errorEl).toContainText(/missing or has already been used/i);

    // Email form visible so user can retry
    await expect(await emailInput(page)).toBeVisible();
  });

  test("'Unable to finish login' maps to friendly message", async ({ page }) => {
    await page.goto("/login?error=Unable+to+finish+login.");

    const errorEl = page.locator("p.error");
    await expect(errorEl).toBeVisible();
    await expect(errorEl).toContainText(/went wrong/i);
    await expect(errorEl).not.toContainText("Unable to finish login");
  });

  test("unknown error gets safe fallback", async ({ page }) => {
    await page.goto("/login?error=some+internal+stack+trace+here");

    const errorEl = page.locator("p.error");
    await expect(errorEl).toBeVisible();
    // Should show the generic fallback, not raw internal text
    await expect(errorEl).toContainText(/problem signing you in/i);
  });
});

// ── success / neutral states ─────────────────────────────────────────────────

test.describe("success states use no error styling", () => {
  test("clean /login has no error element", async ({ page }) => {
    await page.goto("/login");

    await expect(page.locator("p.error")).not.toBeVisible();
    await expect(await emailInput(page)).toBeVisible();
  });
});
