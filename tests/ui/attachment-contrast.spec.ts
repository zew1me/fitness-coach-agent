/**
 * Attachment filename contrast in chat bubbles (issue #584).
 *
 * The filename under an attachment tile, and the timestamp below it, used
 * `--text-muted`. Inside the user bubble that grey sat on the teal accent
 * gradient: in dark mode `#94a3b8` on `#2a9d8f`→`#41b8a9` is about 1.1:1, so
 * the filename was effectively invisible. In the user bubble they now follow
 * the bubble's own text colour, so they read exactly as well as the message.
 *
 * The bubble background is a gradient, so the check computes the WCAG
 * contrast ratio against every colour stop and takes the worst one.
 */
import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { mockAuthenticatedSession, TEST_USER_ID } from "./helpers/session";

const FILENAME = "morning-ride.gpx";

async function mockThreadWithAttachment(page: Page): Promise<void> {
  await page.route("**/api/chat/thread", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        attachments_enabled: true,
        next_cursor: null,
        profile_complete: false,
        thread: {
          id: "thread-test-1",
          messages: ["user", "assistant"].map((role, idx) => ({
            id: `msg-${role}`,
            role,
            thread_id: "thread-test-1",
            user_id: TEST_USER_ID,
            created_at: `2026-01-01T00:00:0${idx + 1}Z`,
            metadata: {
              message_kind: role === "user" ? "user_turn" : "assistant_reply",
            },
            attachments: [],
            parts: [
              { type: "text", text: `${role} message` },
              {
                type: "file",
                mediaType: "application/gpx+xml",
                filename: FILENAME,
                url: "https://example.com/morning-ride.gpx",
              },
            ],
          })),
          state: {},
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:02Z",
          user_id: TEST_USER_ID,
        },
      }),
    }),
  );
}

/**
 * Worst-case WCAG contrast of `el`'s text colour against the nearest
 * `[data-testid="chat-bubble"]` ancestor's background (every gradient stop).
 */
async function worstContrastAgainstBubble(
  page: Page,
  role: "user" | "assistant",
  text: string,
): Promise<{ ratio: number; color: string; messageColor: string }> {
  const bubble = page.locator(
    `[data-testid="chat-bubble"][data-role="${role}"]`,
  );
  const label = bubble.getByText(text, { exact: true });
  await expect(label).toBeVisible();
  return label.evaluate((el) => {
    const parse = (value: string): number[] =>
      (value.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number);
    const luminance = ([r = 0, g = 0, b = 0]: number[]): number => {
      const lin = (c: number): number => {
        const s = c / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
    };
    const contrast = (a: number[], b: number[]): number => {
      const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
      return ((hi ?? 0) + 0.05) / ((lo ?? 0) + 0.05);
    };
    const bubbleEl = el.closest('[data-testid="chat-bubble"]');
    if (!bubbleEl) throw new Error("label is not inside a chat bubble");
    const bubbleStyle = getComputedStyle(bubbleEl);
    const stops = [
      ...bubbleStyle.backgroundImage.matchAll(/rgba?\([^)]*\)/g),
    ].map((m) => m[0]);
    const backgrounds =
      stops.length > 0 ? stops : [bubbleStyle.backgroundColor];
    const fg = parse(getComputedStyle(el).color);
    const messageText = bubbleEl.querySelector("p");
    return {
      ratio: Math.min(...backgrounds.map((bg) => contrast(fg, parse(bg)))),
      color: getComputedStyle(el).color,
      messageColor: messageText ? getComputedStyle(messageText).color : "",
    };
  });
}

for (const colorScheme of ["dark", "light"] as const) {
  test.describe(`attachment filename contrast (${colorScheme} mode)`, () => {
    test.beforeEach(async ({ page }) => {
      await page.emulateMedia({ colorScheme });
      await mockAuthenticatedSession(page);
      await mockThreadWithAttachment(page);
      await page.goto("/");
      await expect(page.locator("html")).toHaveAttribute(
        "data-theme",
        colorScheme,
      );
    });

    test("user-bubble filename reads like the message text", async ({
      page,
    }) => {
      const result = await worstContrastAgainstBubble(page, "user", FILENAME);
      // Same colour as the message body, so the filename is never harder to
      // read than the message it was sent with.
      expect(result.color).toBe(result.messageColor);
      // Before the fix this was ~1.1:1 in dark mode and ~2.3:1 in light mode.
      expect(result.ratio).toBeGreaterThanOrEqual(2.4);
    });

    test("assistant-bubble filename meets WCAG AA", async ({ page }) => {
      const result = await worstContrastAgainstBubble(
        page,
        "assistant",
        FILENAME,
      );
      expect(result.ratio).toBeGreaterThanOrEqual(4.5);
    });
  });
}
