import { describe, expect, it, vi } from "vitest";

import { loadChatMessages } from "../../lib/coach-api";

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
          id: "message-1",
          role: "user",
          parts: [{ type: "text", text: "hello" }],
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
});
