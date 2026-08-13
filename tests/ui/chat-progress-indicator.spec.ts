/**
 * Empty-assistant-bubble regression lock — guards issue #406.
 *
 * The AI SDK pushes a `step-start` part into the live assistant message on
 * `start-step`, before any text or tool part exists. `uiPartText` in
 * `components/coach-chat.tsx` returns null for that part type, so the bubble
 * used to mount visibly empty (timestamp only) until the first text or tool
 * part arrived — which reads to the athlete as a broken app.
 *
 * `MessageBubble` now renders an indeterminate progress indicator whenever a
 * *streaming* assistant bubble has no renderable text, file, or plan content.
 *
 * Run with:  bun run test:ui
 */
import { test, expect } from "@playwright/test";
import { mockAuthenticatedSession } from "./helpers/session";

const ASSISTANT_ID = "asst-fixed-id-progress";
const ASSISTANT_REPLY = "Got it — logged your ride.";
const USER_TEXT = "I rode 40km today";

function sse(chunks: ReadonlyArray<Record<string, unknown>>): string {
  return chunks.map((c) => `data: ${JSON.stringify(c)}\n\n`).join("");
}

/**
 * A turn whose only parts are `step-start` + reasoning — neither is renderable,
 * so before the fix this bubble drew empty. The stream is closed cleanly so the
 * assertion is deterministic; in production this state simply persists for as
 * long as the model reasons before its first text or tool part.
 */
const NO_RENDERABLE_CONTENT =
  sse([
    { type: "start", messageId: ASSISTANT_ID },
    { type: "start-step" },
    { type: "reasoning-start", id: "r0" },
    { type: "reasoning-delta", id: "r0", delta: "considering the ride" },
    { type: "reasoning-end", id: "r0" },
    { type: "finish-step" },
    { type: "finish" },
  ]) + "data: [DONE]\n\n";

/** The same prefix followed by real text — the indicator must give way. */
const PREFIX_THEN_TEXT =
  sse([
    { type: "start", messageId: ASSISTANT_ID },
    { type: "start-step" },
    { type: "text-start", id: "t0" },
    { type: "text-delta", id: "t0", delta: ASSISTANT_REPLY },
    { type: "text-end", id: "t0" },
    { type: "finish-step" },
    { type: "finish" },
  ]) + "data: [DONE]\n\n";

async function setUpChat(
  page: import("@playwright/test").Page,
  streamBody: string,
): Promise<void> {
  await mockAuthenticatedSession(page);

  await page.route("**/api/chat", (route) =>
    route.fulfill({
      status: 200,
      headers: {
        "content-type": "text/event-stream",
        "x-vercel-ai-ui-message-stream": "v1",
        "cache-control": "no-cache",
      },
      body: streamBody,
    }),
  );

  // Thread stays empty: this test is about the *live* bubble, so no persisted
  // rows should ever arrive to mask (or replace) it.
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
          messages: [],
          state: {},
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          user_id: "test-user-123",
        },
      }),
    }),
  );

  await page.route("**/api/chat/messages", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ id: ASSISTANT_ID }),
    }),
  );

  await page.goto("/");
  await expect(page.getByTestId("composer-row")).toBeVisible();
  await page.locator("textarea").fill(USER_TEXT);
  await page.getByRole("button", { name: /send/i }).click();
}

test.describe("streaming assistant bubble progress indicator (#406)", () => {
  test("shows an indicator while the assistant bubble has no renderable content", async ({
    page,
  }) => {
    await setUpChat(page, NO_RENDERABLE_CONTENT);

    const assistantBubble = page.locator(
      '[data-testid="chat-bubble"][data-role="assistant"]',
    );
    await expect(assistantBubble).toHaveCount(1);
    await expect(
      assistantBubble.getByTestId("thinking-indicator"),
    ).toBeVisible();
  });

  test("replaces the indicator once real text streams in", async ({ page }) => {
    await setUpChat(page, PREFIX_THEN_TEXT);

    await expect(page.getByText(ASSISTANT_REPLY)).toBeVisible();
    await expect(page.getByTestId("thinking-indicator")).toHaveCount(0);
  });
});
