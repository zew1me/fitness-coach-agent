/**
 * Mobile chat viewport fit — guards issue #540.
 *
 * On iPhone Safari `100vh` is the *large* viewport (toolbars retracted), so a
 * `min-height: 100vh` shell overflows the visible area and the whole page
 * scrolls. The chat frame must be exactly viewport-tall and let the message
 * pane scroll internally, so the page itself never scrolls.
 */
import { expect, test, type Page } from "@playwright/test";

import { mockAuthenticatedSession, TEST_USER_ID } from "./helpers/session";

const MESSAGE_COUNT = 40;

const PNG_1X1 = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64",
);

async function mockLongThread(page: Page): Promise<void> {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockAuthenticatedSession(page);
  await page.route("**/api/chat/thread", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        attachments_enabled: true,
        next_cursor: null,
        profile_complete: true,
        thread: {
          id: "thread-test-1",
          user_id: TEST_USER_ID,
          state: {},
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          messages: Array.from({ length: MESSAGE_COUNT }, (_, i) => ({
            id: `msg-${i}`,
            role: i % 2 === 0 ? "user" : "assistant",
            thread_id: "thread-test-1",
            user_id: TEST_USER_ID,
            created_at: "2026-01-01T00:00:00Z",
            metadata: {},
            attachments: [],
            parts: [{ type: "text", text: `Message ${i}: keep it steady.` }],
          })),
        },
      }),
    }),
  );
}

test.describe("mobile chat viewport fit (#540)", () => {
  test("a long thread scrolls inside the message pane, not the page", async ({
    page,
  }) => {
    await mockLongThread(page);

    await page.goto("/");
    await expect(page.getByText("Message 0: keep it steady.")).toBeAttached();

    const { scrollHeight, innerHeight } = await page.evaluate(() => ({
      scrollHeight: document.documentElement.scrollHeight,
      innerHeight: window.innerHeight,
    }));
    expect(scrollHeight).toBeLessThanOrEqual(innerHeight);

    // The transcript must stay reachable: it scrolls inside the pane.
    const pane = page.locator('[class*="messagesPane"]');
    const overflow = await pane.evaluate((el) => ({
      clientHeight: el.clientHeight,
      scrollHeight: el.scrollHeight,
    }));
    expect(overflow.scrollHeight).toBeGreaterThan(overflow.clientHeight);
    await pane.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
    await expect(
      page.getByText(`Message ${MESSAGE_COUNT - 1}: keep it steady.`),
    ).toBeInViewport();
  });

  test("many attachment chips scroll inside the composer, keeping Send on screen", async ({
    page,
  }) => {
    await mockLongThread(page);
    await page.goto("/");
    const label = page.getByLabel("Add photo or activity file");
    const [fileChooser] = await Promise.all([
      page.waitForEvent("filechooser"),
      label.click(),
    ]);
    await fileChooser.setFiles(
      Array.from({ length: 30 }, (_, i) => ({
        name: `workout-${i}.png`,
        mimeType: "image/png",
        buffer: PNG_1X1,
      })),
    );
    await expect(page.getByText("workout-29.png")).toBeAttached();

    const send = page.getByRole("button", { name: /^Send$/i });
    const box = await send.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.y + box!.height).toBeLessThanOrEqual(844);
    const { scrollHeight, innerHeight } = await page.evaluate(() => ({
      scrollHeight: document.documentElement.scrollHeight,
      innerHeight: window.innerHeight,
    }));
    expect(scrollHeight).toBeLessThanOrEqual(innerHeight);
  });
});
