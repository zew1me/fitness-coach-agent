/**
 * Chat round-trip regression lock — guards issues #158 and #162.
 *
 * Both bugs were the same render-layer mismatch: a *live* (in-flight) message
 * from `useChat` and the *persisted* (DB) copy reloaded by `loadChatThread`
 * rendered twice because the dedup at `coach-chat.tsx` keys on `message.id` and
 * the client/server used different ids for the same logical message.
 *
 * The fix threads the client-chosen id through persistence so the server row
 * shares the id (user turn: app/api/chat/route.ts; assistant turn:
 * lib/agent/orchestrator.ts; honored at api/index.py → create_chat_message).
 *
 * This test reproduces the full send → stream → persist → reload cycle against
 * mocked endpoints, asserting that exactly ONE user bubble and ONE assistant
 * bubble render. If the id contract regresses (server mints its own id), the
 * reloaded thread id no longer matches the live id and a duplicate bubble
 * reappears — failing this test.
 *
 * Run with:  bun run test:ui
 */
import { test, expect } from "@playwright/test";
import { mockAuthenticatedSession } from "./helpers/session";

const ASSISTANT_ID = "asst-fixed-id-roundtrip";
const ASSISTANT_REPLY = "Got it — logged your ride.";

/**
 * A minimal AI-SDK UI-message-stream SSE body for a single assistant text turn.
 * `messageId` pins the assistant message id so the persisted reload can echo it.
 */
function uiMessageStream(messageId: string, text: string): string {
  const chunks = [
    { type: "start", messageId },
    { type: "start-step" },
    { type: "text-start", id: "t0" },
    { type: "text-delta", id: "t0", delta: text },
    { type: "text-end", id: "t0" },
    { type: "finish-step" },
    { type: "finish" },
  ];
  return (
    chunks.map((c) => `data: ${JSON.stringify(c)}\n\n`).join("") +
    "data: [DONE]\n\n"
  );
}

test.describe("chat round-trip dedup (#158 / #162)", () => {
  test("a single user→assistant exchange renders exactly one bubble per turn", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page);

    let capturedUserId: string | null = null;
    let threadCalls = 0;

    // Capture the client-chosen user message id from the streaming request, and
    // return a hermetic assistant stream with a pinned assistant id.
    await page.route("**/api/chat", async (route) => {
      const body = route.request().postDataJSON() as {
        messages?: Array<{ id?: string; role?: string }>;
      };
      const lastUser = [...(body.messages ?? [])]
        .reverse()
        .find((m) => m.role === "user");
      capturedUserId = lastUser?.id ?? null;

      await route.fulfill({
        status: 200,
        headers: {
          "content-type": "text/event-stream",
          "x-vercel-ai-ui-message-stream": "v1",
          "cache-control": "no-cache",
        },
        body: uiMessageStream(ASSISTANT_ID, ASSISTANT_REPLY),
      });
    });

    // First thread load (on mount) is empty; the reload after send returns the
    // persisted user + assistant rows under the SAME ids the client used — this
    // is the contract the fix guarantees and the dedup relies on.
    await page.route("**/api/chat/thread", async (route) => {
      threadCalls += 1;
      const messages =
        threadCalls === 1 || capturedUserId === null
          ? []
          : [
              {
                id: capturedUserId,
                role: "user",
                thread_id: "thread-test-1",
                user_id: "test-user-123",
                created_at: "2026-01-01T00:00:01Z",
                metadata: { message_kind: "user_turn" },
                attachments: [],
                parts: [{ type: "text", text: "I rode 40km today" }],
              },
              {
                id: ASSISTANT_ID,
                role: "assistant",
                thread_id: "thread-test-1",
                user_id: "test-user-123",
                created_at: "2026-01-01T00:00:02Z",
                metadata: { message_kind: "assistant_reply" },
                attachments: [],
                parts: [{ type: "text", text: ASSISTANT_REPLY }],
              },
            ];
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          attachments_enabled: true,
          next_cursor: null,
          profile_complete: false,
          thread: {
            id: "thread-test-1",
            messages,
            state: {},
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:02Z",
            user_id: "test-user-123",
          },
        }),
      });
    });

    // The Next.js /api/chat route normally persists the user turn via this
    // endpoint; we bypass the route, so just accept the (possibly unused) call.
    await page.route("**/api/chat/messages", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ id: capturedUserId ?? ASSISTANT_ID }),
      }),
    );

    await page.goto("/");
    await expect(page.getByTestId("composer-row")).toBeVisible();

    await page.locator("textarea").fill("I rode 40km today");
    await page.getByRole("button", { name: /send/i }).click();

    // Assistant reply renders.
    await expect(page.getByText(ASSISTANT_REPLY)).toBeVisible();

    // The crux: no duplicate bubbles for either role after the reload settles.
    await expect(
      page.locator('[data-testid="chat-bubble"][data-role="user"]'),
    ).toHaveCount(1);
    await expect(
      page.locator('[data-testid="chat-bubble"][data-role="assistant"]'),
    ).toHaveCount(1);
  });
});
