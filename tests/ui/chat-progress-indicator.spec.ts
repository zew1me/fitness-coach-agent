/**
 * Empty-assistant-bubble regression lock — guards issue #406.
 *
 * The AI SDK pushes a `step-start` part into the live assistant message on
 * `start-step`, before any text or tool part exists (reasoning parts behave the
 * same way). `uiPartText` in `components/coach-chat.tsx` returns null for both,
 * so the bubble used to mount visibly empty (timestamp only) until the first
 * text or tool part arrived — which reads to the athlete as a broken app.
 *
 * `MessageBubble` now renders an indeterminate progress indicator whenever a
 * *streaming* assistant bubble has no renderable text, file, or plan content.
 *
 * The stream here is served by a local SSE server the test drives chunk by
 * chunk, so the assertions observe the real mid-stream transition (indicator
 * shown → indicator replaced by text) on a single message rather than two
 * separate end states. `route.fulfill` cannot do this: it can only deliver a
 * complete body.
 *
 * Run with:  bun run test:ui
 */
import { createServer, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";

import { test, expect, type Page } from "@playwright/test";

import { mockAuthenticatedSession } from "./helpers/session";

const ASSISTANT_ID = "asst-fixed-id-progress";
const ASSISTANT_REPLY = "Got it — logged your ride.";
const USER_TEXT = "I rode 40km today";

/** Parts that exist but render nothing — the #406 trigger. */
const PRE_CONTENT_CHUNKS = [
  { type: "start", messageId: ASSISTANT_ID },
  { type: "start-step" },
  { type: "reasoning-start", id: "r0" },
  { type: "reasoning-delta", id: "r0", delta: "considering the ride" },
  { type: "reasoning-end", id: "r0" },
] as const;

/** The first renderable content, which must displace the indicator. */
const TEXT_CHUNKS = [
  { type: "text-start", id: "t0" },
  { type: "text-delta", id: "t0", delta: ASSISTANT_REPLY },
  { type: "text-end", id: "t0" },
  { type: "finish-step" },
  { type: "finish" },
] as const;

function sse(chunks: ReadonlyArray<Record<string, unknown>>): string {
  return chunks.map((c) => `data: ${JSON.stringify(c)}\n\n`).join("");
}

/**
 * An SSE endpoint whose response is held open until the test pushes to it.
 * `write()` flushes a batch of chunks; `end()` closes the stream.
 */
type ChatStream = {
  origin: string;
  connected: Promise<void>;
  write: (chunks: ReadonlyArray<Record<string, unknown>>) => void;
  end: () => void;
  close: () => Promise<void>;
};

async function startChatStreamServer(): Promise<ChatStream> {
  let response: ServerResponse | null = null;
  let onConnect: () => void = () => {};
  const connected = new Promise<void>((resolve) => {
    onConnect = resolve;
  });

  const server: Server = createServer((_req, res) => {
    res.writeHead(200, {
      "content-type": "text/event-stream",
      "x-vercel-ai-ui-message-stream": "v1",
      "cache-control": "no-cache",
      connection: "keep-alive",
    });
    // Chromium buffers the first bytes of a response before surfacing them to
    // fetch(); an immediate comment line flushes the headers so the client's
    // stream reader starts consuming right away.
    res.write(": open\n\n");
    response = res;
    onConnect();
  });

  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;

  return {
    origin: `http://127.0.0.1:${port}`,
    connected,
    write: (chunks) => response?.write(sse(chunks)),
    end: () => {
      response?.write("data: [DONE]\n\n");
      response?.end();
    },
    close: () =>
      new Promise<void>((resolve) => {
        response?.end();
        server.close(() => resolve());
      }),
  };
}

async function sendUserTurn(page: Page, stream: ChatStream): Promise<void> {
  await mockAuthenticatedSession(page);

  // Redirect the chat POST at the local SSE server so the test controls when
  // each chunk lands.
  await page.route("**/api/chat", (route) =>
    route.continue({ url: `${stream.origin}/chat` }),
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
  test("shows an indicator until real content arrives mid-stream", async ({
    page,
  }) => {
    const stream = await startChatStreamServer();

    try {
      await sendUserTurn(page, stream);
      await stream.connected;

      // Phase 1 — the bubble exists but has nothing renderable in it.
      stream.write(PRE_CONTENT_CHUNKS);

      const assistantBubble = page.locator(
        '[data-testid="chat-bubble"][data-role="assistant"]',
      );
      await expect(assistantBubble).toHaveCount(1);
      const indicator = assistantBubble.getByTestId("thinking-indicator");
      await expect(indicator).toBeVisible();
      await expect(assistantBubble).not.toContainText(ASSISTANT_REPLY);

      // Phase 2 — the first text delta lands on that same message.
      stream.write(TEXT_CHUNKS);
      stream.end();

      await expect(assistantBubble).toContainText(ASSISTANT_REPLY);
      await expect(indicator).toHaveCount(0);
    } finally {
      await stream.close();
    }
  });
});
