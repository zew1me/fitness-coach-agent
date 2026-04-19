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

  it("executes calculate_zones by calling the engine zones endpoint", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ zones: [{ name: "Endurance", zone: 2 }] }), {
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

    const calculateZones = tools["calculate_zones"] as {
      // eslint-disable-next-line no-unused-vars
      execute: (...args: unknown[]) => Promise<unknown>;
    };

    await expect(
      calculateZones.execute({
        ftp_watts: 300,
        sport: "cycling",
        user_id: "athlete-1"
      })
    ).resolves.toEqual({ zones: [{ name: "Endurance", zone: 2 }] });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost/api/engine/calculate-zones",
      expect.objectContaining({
        body: JSON.stringify({
          ftp_watts: 300,
          sport: "cycling"
        }),
        method: "POST"
      })
    );
  });

  it("executes estimate_thresholds by calling the engine thresholds endpoint", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ ftp_watts: 285, lt1_watts: 214, sport: "cycling" }), {
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

    const estimateThresholds = tools["estimate_thresholds"] as {
      // eslint-disable-next-line no-unused-vars
      execute: (...args: unknown[]) => Promise<unknown>;
    };

    await expect(
      estimateThresholds.execute({
        sport: "cycling",
        test_duration_minutes: 20,
        test_power_watts: 300,
        user_id: "athlete-1"
      })
    ).resolves.toEqual({ ftp_watts: 285, lt1_watts: 214, sport: "cycling" });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost/api/engine/estimate-thresholds",
      expect.objectContaining({
        body: JSON.stringify({
          sport: "cycling",
          test_duration_minutes: 20,
          test_power_watts: 300
        }),
        method: "POST"
      })
    );
  });
});
