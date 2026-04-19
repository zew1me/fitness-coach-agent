import { describe, expect, it, vi } from "vitest";

import { createCoachTools, POST } from "../../app/api/chat/route";

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

  it("executes get_athlete_context by calling the engine summary endpoint", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ profile: { user_id: "athlete-1" } }), {
          status: 200,
          headers: { "content-type": "application/json" }
        })
      )
    );
    const tools = createCoachTools({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      fetchImpl: fetchMock as unknown as typeof fetch,
      userId: "athlete-1"
    });

    const getAthleteContext = tools["get_athlete_context"] as {
      // eslint-disable-next-line no-unused-vars
      execute: (...args: unknown[]) => Promise<unknown>;
    };

    await expect(getAthleteContext.execute({ user_id: "athlete-1" })).resolves.toEqual({
      profile: { user_id: "athlete-1" }
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost/api/engine/get-athlete-summary",
      expect.objectContaining({
        method: "POST"
      })
    );
  });
});
