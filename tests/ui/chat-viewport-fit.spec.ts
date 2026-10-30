/**
 * Mobile chat viewport fit — guards issue #540.
 *
 * On iPhone Safari `100vh` is the *large* viewport (toolbars retracted), so a
 * `min-height: 100vh` shell overflows the visible area and the whole page
 * scrolls. The chat frame must be exactly viewport-tall and let the message
 * pane scroll internally, so the page itself never scrolls.
 */
import { expect, test } from "@playwright/test";

import { mockAuthenticatedSession, TEST_USER_ID } from "./helpers/session";

const MESSAGE_COUNT = 40;

test.describe("mobile chat viewport fit (#540)", () => {
  test("a long thread scrolls inside the message pane, not the page", async ({
    page,
  }) => {
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

    await page.goto("/");
    await expect(page.getByText("Message 0: keep it steady.")).toBeAttached();

    const { scrollHeight, innerHeight } = await page.evaluate(() => ({
      scrollHeight: document.documentElement.scrollHeight,
      innerHeight: window.innerHeight,
    }));
    expect(scrollHeight).toBeLessThanOrEqual(innerHeight);
  });
});
