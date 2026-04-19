import { describe, expect, it } from "vitest";

import { POST } from "../../app/api/chat/route";

describe("app/api/chat route", () => {
  it("returns 401 when the browser session cookie is absent", async () => {
    const response = await POST(
      new Request("http://localhost/api/chat", {
        method: "POST",
        body: JSON.stringify({ messages: [] })
      })
    );

    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({
      error: "Missing browser session cookie."
    });
  });
});
