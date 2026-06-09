/**
 * Attachment button UI tests — covers the label+input fix for issue #41.
 *
 * The fix replaced a `<button>` that called `fileInputRef.current?.click()`
 * with a `<label>` wrapping the file input directly. This is more reliable
 * across browsers (iOS Safari blocks programmatic .click() on hidden inputs).
 *
 * Run with:  bun run test:ui
 * Requires the dev server to be running on http://localhost:3000 (or BASE_URL).
 */
import { test, expect } from "@playwright/test";
import { mockAuthenticatedSession } from "./helpers/session";

// ── helpers ───────────────────────────────────────────────────────────────────

async function attachLabel(page: import("@playwright/test").Page) {
  return page.getByLabel("Add photo");
}

// ── structure & accessibility ─────────────────────────────────────────────────

test.describe("attachment button structure", () => {
  test("renders as a label (not a button) with correct aria-label", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page);
    await page.goto("/");

    const label = await attachLabel(page);
    await expect(label).toBeVisible();

    // Must be a <label>, not a <button>
    expect(await label.evaluate((el) => el.tagName.toLowerCase())).toBe(
      "label",
    );
    await expect(label).toHaveAttribute("title", "Add photo or activity file");
  });

  test("label contains the file input accepting images and activity files", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page);
    await page.goto("/");

    const label = await attachLabel(page);
    await expect(label).toBeVisible();

    const fileInput = label.locator("input[type='file']");
    // CHAT_ATTACHMENT_ACCEPT in components/coach-chat.tsx — images plus GPX/FIT/TCX.
    await expect(fileInput).toHaveAttribute(
      "accept",
      "image/*,application/gpx+xml,.gpx,.fit,.tcx",
    );
    await expect(fileInput).toHaveAttribute("multiple");
  });
});

// ── interaction ───────────────────────────────────────────────────────────────

test.describe("attachment button interaction", () => {
  test("clicking the label opens the file chooser", async ({ page }) => {
    await mockAuthenticatedSession(page);
    await page.goto("/");

    const label = await attachLabel(page);
    await expect(label).toBeVisible();

    // If the label+input wiring is correct, clicking it triggers a file chooser.
    // This is the regression guard for the iOS Safari bug where
    // programmatic fileInputRef.current?.click() was blocked.
    const [fileChooser] = await Promise.all([
      page.waitForEvent("filechooser"),
      label.click(),
    ]);

    expect(fileChooser).toBeTruthy();
  });

  test("selecting a file via the chooser shows an attachment chip", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page);
    await page.goto("/");

    const label = await attachLabel(page);
    await expect(label).toBeVisible();

    // Create a minimal 1×1 PNG to attach (real file, not a path fixture)
    const [fileChooser] = await Promise.all([
      page.waitForEvent("filechooser"),
      label.click(),
    ]);

    await fileChooser.setFiles({
      name: "workout.png",
      mimeType: "image/png",
      // 1×1 transparent PNG bytes
      buffer: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
        "base64",
      ),
    });

    // The upload chip with the filename should appear
    await expect(page.locator("text=workout.png")).toBeVisible();
  });
});
