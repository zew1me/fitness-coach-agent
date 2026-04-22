import { readFileSync } from "node:fs";

import { afterEach, describe, expect, it, vi } from "vitest";

import { POST } from "../../app/api/chat/route";
import { createCoachTools } from "../../lib/agent/coach-tools";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("app/api/chat route", () => {
  it("uses the GPT-5 mini model for chat responses", () => {
    const routeSource = readFileSync(new URL("../../app/api/chat/route.ts", import.meta.url), "utf8");

    expect(routeSource).toContain('openai("gpt-5-mini")');
  });

  it("keeps short conversations intact for model context", () => {
    const messages = Array.from({ length: 4 }, (_, index) => ({
      id: `message-${index}`,
      parts: [{ text: `Message ${index}`, type: "text" as const }],
      role: index % 2 === 0 ? ("user" as const) : ("assistant" as const)
    }));

    expect(selectMessagesForModel(messages)).toEqual(messages);
  });

  it("compacts long conversations to a recent model context window", () => {
    const messages = Array.from({ length: 40 }, (_, index) => ({
      id: `message-${index}`,
      parts: [{ text: `Message ${index}`, type: "text" as const }],
      role: index % 2 === 0 ? ("user" as const) : ("assistant" as const)
    }));

    const selected = selectMessagesForModel(messages);

    expect(selected).toHaveLength(25);
    expect(selected[0]).toMatchObject({
      id: "context-window-notice",
      role: "system",
      parts: [
        {
          text: expect.stringContaining("previous 16 chat messages"),
          type: "text"
        }
      ]
    });
    expect(selected.slice(1).map((message) => message.id)).toEqual(
      messages.slice(16).map((message) => message.id)
    );
  });

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

  it("returns a bounded 503 when the browser token proxy connection resets", async () => {
    const error = Object.assign(new Error("socket hang up"), { code: "ECONNRESET" });
    const fetchMock = vi.fn(() => Promise.reject(error));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const response = await POST(
      new Request("http://localhost/api/chat", {
        method: "POST",
        headers: { cookie: "coach_browser_session=session-token" },
        body: JSON.stringify({ messages: [] })
      })
    );

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toEqual({
      error: "Something went wrong. Please refresh and try again."
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost/api/oauth/browser-token",
      expect.objectContaining({
        method: "POST"
      })
    );
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

  it("executes get_active_plan by reading it from the athlete summary", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ active_plan: { id: "plan-1", title: "Base build" } }), {
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

    const getActivePlan = tools["get_active_plan"] as {
      execute: (...args: unknown[]) => Promise<unknown>;
    };

    await expect(getActivePlan.execute({ user_id: "athlete-1" })).resolves.toEqual({
      active_plan: { id: "plan-1", title: "Base build" }
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost/api/engine/get-athlete-summary",
      expect.objectContaining({
        method: "POST"
      })
    );
  });

  it("executes get_recent_activities by calling the engine activities endpoint", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ activities: [{ id: "activity-1", sport: "running" }] }), {
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

    const getRecentActivities = tools["get_recent_activities"] as {
      execute: (...args: unknown[]) => Promise<unknown>;
    };

    await expect(
      getRecentActivities.execute({
        limit: 3,
        sport: "running",
        user_id: "athlete-1"
      })
    ).resolves.toEqual({ activities: [{ id: "activity-1", sport: "running" }] });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost/api/engine/get-recent-activities",
      expect.objectContaining({
        body: JSON.stringify({
          limit: 3,
          sport: "running",
          user_id: "athlete-1"
        }),
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

  it("executes generate_training_plan by calling the engine plan structure endpoint", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ phases: [{ name: "Base" }], total_weeks: 8 }), {
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

    const generateTrainingPlan = tools["generate_training_plan"] as {
      execute: (...args: unknown[]) => Promise<unknown>;
    };

    await expect(
      generateTrainingPlan.execute({
        goal_id: "goal-1",
        user_id: "athlete-1"
      })
    ).resolves.toEqual({ phases: [{ name: "Base" }], total_weeks: 8 });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost/api/engine/generate-plan-structure",
      expect.objectContaining({
        body: JSON.stringify({
          goal_id: "goal-1",
          user_id: "athlete-1"
        }),
        method: "POST"
      })
    );
  });

  it("executes process_uploaded_file for screenshots by calling the screenshot analyzer", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ data: { sport: "running" }, screenshot_type: "activity_single" }), {
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

    const processUploadedFile = tools["process_uploaded_file"] as {
      execute: (...args: unknown[]) => Promise<unknown>;
    };

    await expect(
      processUploadedFile.execute({
        content_type: "image/png",
        filename: "activity.png",
        object_key: "uploads/activity.png",
        public_url: "https://example.com/activity.png",
        user_id: "athlete-1"
      })
    ).resolves.toEqual({ data: { sport: "running" }, screenshot_type: "activity_single" });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost/api/engine/analyze-screenshot",
      expect.objectContaining({
        body: JSON.stringify({
          image_url: "https://example.com/activity.png"
        }),
        method: "POST"
      })
    );
  });

  it("executes process_uploaded_file for activity files by calling the activity parser", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ activity: { sport: "running", distance_meters: 5000 } }), {
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

    const processUploadedFile = tools["process_uploaded_file"] as {
      execute: (...args: unknown[]) => Promise<unknown>;
    };

    const result = processUploadedFile.execute({
        content_type: "application/gpx+xml",
        filename: "morning-run.gpx",
        object_key: "users/athlete-1/chat-attachment/2026/04/19/morning-run.gpx",
        public_url: "https://example.com/morning-run.gpx",
        user_id: "attacker-controlled-user"
      });

    await expect(Promise.resolve(result)).resolves.toEqual({
      activity: { sport: "running", distance_meters: 5000 }
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost/api/engine/process-uploaded-file",
      expect.objectContaining({
        body: JSON.stringify({
          content_type: "application/gpx+xml",
          filename: "morning-run.gpx",
          object_key: "users/athlete-1/chat-attachment/2026/04/19/morning-run.gpx",
          public_url: "https://example.com/morning-run.gpx",
          user_id: "athlete-1"
        }),
        method: "POST"
      })
    );
  });

});
