import { describe, expect, it, vi } from "vitest";

import { loadChatMessages, loadChatThread } from "../../lib/coach-api";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("loadChatMessages", () => {
  it("rejects malformed paginated message responses", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ access_token: "token", user_id: "athlete-1" }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ messages: [{ id: "message-1" }], next_cursor: 42 }),
      );

    await expect(loadChatMessages("cursor", fetchMock)).rejects.toThrow();
  });

  it("returns a valid paginated message response", async () => {
    const page = {
      messages: [
        {
          attachments: [],
          content: "",
          created_at: "2026-04-04T09:00:00Z",
          id: "message-1",
          metadata: {},
          role: "user",
          parts: [{ type: "text", text: "hello" }],
          thread_id: "thread-1",
          user_id: "athlete-1",
        },
      ],
      next_cursor: null,
    };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ access_token: "token", user_id: "athlete-1" }),
      )
      .mockResolvedValueOnce(jsonResponse(page));

    await expect(loadChatMessages("cursor", fetchMock)).resolves.toEqual(page);
  });

  it("rejects paginated messages missing persisted message fields", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ access_token: "token", user_id: "athlete-1" }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          messages: [
            {
              id: "message-1",
              role: "user",
              parts: [{ type: "text", text: "hello" }],
            },
          ],
          next_cursor: null,
        }),
      );

    await expect(loadChatMessages("cursor", fetchMock)).rejects.toThrow();
  });
});

describe("loadChatThread", () => {
  it("rejects malformed thread responses", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ access_token: "token", user_id: "athlete-1" }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          attachments_enabled: true,
          profile_complete: true,
          thread: { id: "thread-1", messages: [] },
        }),
      );

    await expect(loadChatThread(fetchMock)).rejects.toThrow();
  });

  it("returns a valid thread response", async () => {
    const thread = {
      attachments_enabled: true,
      next_cursor: null,
      profile_complete: true,
      thread: {
        created_at: "2026-04-04T09:00:00Z",
        id: "thread-1",
        messages: [],
        state: {},
        updated_at: "2026-04-04T09:00:00Z",
        user_id: "athlete-1",
      },
    };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ access_token: "token", user_id: "athlete-1" }),
      )
      .mockResolvedValueOnce(jsonResponse(thread));

    await expect(loadChatThread(fetchMock)).resolves.toEqual(thread);
  });
});
