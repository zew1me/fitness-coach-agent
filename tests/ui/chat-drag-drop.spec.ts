/**
 * Drag-and-drop attachment tests — regression guard for issue #161.
 *
 * Before the fix, dropping a file onto the composer made the browser navigate
 * to the file URL (default <textarea> drop behavior) instead of attaching it.
 * The fix adds dragover/drop handlers on the composer wrapper that preventDefault
 * and route the dropped files through the existing `handleFilesAdded` path.
 *
 * Run with:  bun run test:ui
 */
import { test, expect } from "@playwright/test";
import { mockAuthenticatedSession, ONE_BY_ONE_PNG_BASE64 } from "./helpers/session";

/**
 * Synthesize a real DataTransfer-backed `drop` (with `dragover` first, which the
 * handler needs to preventDefault) on the composer wrapper. Playwright has no
 * native file-drop API, so we build the File + DataTransfer in the page context.
 */
async function dropFileOnComposer(
  page: import("@playwright/test").Page,
  file: { name: string; type: string; base64: string },
): Promise<void> {
  await page.evaluate(
    ({ name, type, base64 }) => {
      const target = document.querySelector('[data-testid="composer-row"]');
      if (!target) throw new Error("composer-row not found");

      const binary = atob(base64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
      const droppedFile = new File([bytes], name, { type });

      const dataTransfer = new DataTransfer();
      dataTransfer.items.add(droppedFile);

      for (const eventType of ["dragenter", "dragover", "drop"]) {
        const event = new DragEvent(eventType, {
          bubbles: true,
          cancelable: true,
          dataTransfer,
        });
        target.dispatchEvent(event);
      }
    },
    file,
  );
}

test.describe("composer drag-and-drop (#161)", () => {
  test("dropping a supported image attaches a chip and does not navigate", async ({ page }) => {
    await mockAuthenticatedSession(page);
    await page.goto("/");
    await expect(page.getByTestId("composer-row")).toBeVisible();

    await dropFileOnComposer(page, {
      name: "workout.png",
      type: "image/png",
      base64: ONE_BY_ONE_PNG_BASE64,
    });

    // The attachment chip with the filename should appear…
    await expect(page.locator("text=workout.png")).toBeVisible();
    // …and the browser must still be on the chat page (no navigation to the file).
    await expect(page).toHaveURL(/\/$/);
    expect(page.url()).not.toContain("blob:");
    expect(page.url()).not.toContain("workout.png");
  });

  test("dropping an unsupported file surfaces an error and attaches nothing", async ({ page }) => {
    await mockAuthenticatedSession(page);
    await page.goto("/");
    await expect(page.getByTestId("composer-row")).toBeVisible();

    await dropFileOnComposer(page, {
      name: "notes.txt",
      type: "text/plain",
      base64: btoaNode("hello"),
    });

    // No chip for the rejected file.
    await expect(page.locator("text=notes.txt")).toHaveCount(0);
    // The existing unsupported-type error is surfaced to the user.
    await expect(page.getByText(/image, GPX, FIT, and TCX attachments are supported/i)).toBeVisible();
    await expect(page).toHaveURL(/\/$/);
  });
});

/** Node-side base64 (the page-side decode uses atob). */
function btoaNode(s: string): string {
  return Buffer.from(s, "utf-8").toString("base64");
}
