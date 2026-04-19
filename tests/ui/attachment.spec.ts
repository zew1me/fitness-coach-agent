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

// ── API mocks ─────────────────────────────────────────────────────────────────

async function mockAuthenticatedSession(
  page: import("@playwright/test").Page,
): Promise<void> {
  await page.route("**/api/oauth/browser-token", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        user_id: "test-user-123",
        access_token: "fake-token-for-tests",
      }),
    }),
  );

  await page.route("**/api/chat/thread", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        thread: {
          id: "thread-test-1",
          messages: [],
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      }),
    }),
  );

  await page.route("**/api/engine/get-athlete-summary", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        profile: {
          user_id: "test-user-123",
          coaching_state: "onboarding",
          primary_sports: [],
        },
      }),
    }),
  );

  await page.route("**/api/chat/attachments/presign", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        upload_url: "https://example.com/upload",
        object_key: "test-file-key",
        public_url: "https://example.com/test-image.png",
        method: "PUT",
        headers: {},
      }),
    }),
  );

  // Intercept the actual file upload so tests are hermetic
  await page.route("https://example.com/upload", (route) =>
    route.fulfill({ status: 200 }),
  );
}

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
    await expect(label).toHaveAttribute("title", "Add photo");
  });

  test("label contains the file input with accept='image/*'", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page);
    await page.goto("/");

    const label = await attachLabel(page);
    await expect(label).toBeVisible();

    const fileInput = label.locator("input[type='file']");
    await expect(fileInput).toHaveAttribute("accept", "image/*");
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
