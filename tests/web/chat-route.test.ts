import { readFileSync } from "node:fs";

import { afterEach, describe, expect, it, vi } from "vitest";

import { POST } from "../../app/api/chat/route";
import { createCoachTools } from "../../lib/agent/coach-tools";
import {
  appendImageExtractionsToMessages,
  selectMessagesForModel
} from "../../lib/agent/message-context";
import { streamCoachTurn } from "../../lib/agent/orchestrator";

import { athleteContextFixture } from "./agent-fixtures";

vi.mock("../../lib/agent/orchestrator", () => ({
  streamCoachTurn: vi.fn(() =>
    Promise.resolve(
      new Response("coach stream", {
        headers: { "content-type": "text/plain" },
        status: 200,
      })
    )
  ),
}));

const originalFetch = globalThis.fetch;
const originalVercelBypassSecret = process.env["VERCEL_AUTOMATION_BYPASS_SECRET"];

afterEach(() => {
  globalThis.fetch = originalFetch;
  if (originalVercelBypassSecret === undefined) {
    delete process.env["VERCEL_AUTOMATION_BYPASS_SECRET"];
  } else {
    process.env["VERCEL_AUTOMATION_BYPASS_SECRET"] = originalVercelBypassSecret;
  }
  vi.clearAllMocks();
});

describe("app/api/chat route", () => {
  it("delegates authenticated chat turns to the orchestrator", async () => {
    const messages = [
      {
        id: "message-1",
        parts: [{ text: "Can you adjust tomorrow's workout?", type: "text" as const }],
        role: "user" as const,
      },
    ];
    const fetchMock = vi.fn((url: RequestInfo | URL) => {
      if (String(url).endsWith("/api/oauth/browser-token")) {
        return Promise.resolve(
          new Response(JSON.stringify({ access_token: "token-1", user_id: "athlete-1" }), {
            headers: { "content-type": "application/json" },
            status: 200,
          })
        );
      }

      return Promise.resolve(
        new Response(JSON.stringify(athleteContextFixture), {
          headers: { "content-type": "application/json" },
          status: 200,
        })
      );
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const response = await POST(
      new Request("http://localhost/api/chat", {
        body: JSON.stringify({ messages }),
        headers: { cookie: "coach_browser_session=session-token" },
        method: "POST",
      })
    );

    expect(response.status).toBe(200);
    await expect(response.text()).resolves.toBe("coach stream");
    expect(streamCoachTurn).toHaveBeenCalledWith(
      expect.objectContaining({
        accessToken: "token-1",
        baseUrl: "http://localhost",
        context: athleteContextFixture,
        extraHeaders: {},
        messages,
        messagesAreModelSelected: true,
        streamErrorMessage: "Coach is unavailable right now. Please try again.",
      })
    );
  });

  it("persists the latest user turn to the backend before streaming", async () => {
    const messages = [
      {
        id: "older-message",
        parts: [{ text: "Earlier reply", type: "text" as const }],
        role: "assistant" as const,
      },
      {
        id: "63ff9606-9158-43d7-a82b-d31ef9788b7d",
        parts: [{ text: "I train ~8 hours/week", type: "text" as const }],
        role: "user" as const,
      },
    ];
    const persistRequests: { url: string; body: unknown; headers: Record<string, string> }[] = [];
    const fetchMock = vi.fn((url: RequestInfo | URL, init?: RequestInit) => {
      const urlStr = String(url);
      if (urlStr.endsWith("/api/oauth/browser-token")) {
        return Promise.resolve(
          new Response(JSON.stringify({ access_token: "token-1", user_id: "athlete-1" }), {
            headers: { "content-type": "application/json" },
            status: 200,
          })
        );
      }
      if (urlStr.endsWith("/api/chat/messages")) {
        persistRequests.push({
          url: urlStr,
          body: JSON.parse(String(init?.body ?? "{}")),
          headers: Object.fromEntries(new Headers(init?.headers).entries()),
        });
        return Promise.resolve(
          new Response(JSON.stringify({ id: "message-1" }), {
            headers: { "content-type": "application/json" },
            status: 200,
          })
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify(athleteContextFixture), {
          headers: { "content-type": "application/json" },
          status: 200,
        })
      );
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    await POST(
      new Request("http://localhost/api/chat", {
        body: JSON.stringify({ messages }),
        headers: { cookie: "coach_browser_session=session-token" },
        method: "POST",
      })
    );

    expect(persistRequests).toHaveLength(1);
    expect(persistRequests[0]?.body).toEqual({
      id: "63ff9606-9158-43d7-a82b-d31ef9788b7d",
      role: "user",
      parts: [{ text: "I train ~8 hours/week", type: "text" }],
      metadata: {
        message_kind: "user_turn",
        client_message_id: "63ff9606-9158-43d7-a82b-d31ef9788b7d"
      },
    });
    expect(persistRequests[0]?.headers["authorization"]).toBe("Bearer token-1");
  });

  it("forwards file parts verbatim on the persisted user turn", async () => {
    const messages = [
      {
        id: "46db0714-d6d8-402b-a421-00b21b3a29f6",
        parts: [
          { text: "Here's my chart", type: "text" as const },
          {
            filename: "fitness.png",
            mediaType: "image/png",
            type: "file" as const,
            url: "https://r2.example.com/users/athlete-1/chat-attachment/2026/04/19/fitness.png",
          },
        ],
        role: "user" as const,
      },
    ];
    const persistBodies: unknown[] = [];
    const fetchMock = vi.fn((url: RequestInfo | URL, init?: RequestInit) => {
      const urlStr = String(url);
      if (urlStr.endsWith("/api/oauth/browser-token")) {
        return Promise.resolve(
          new Response(JSON.stringify({ access_token: "token-1", user_id: "athlete-1" }), {
            headers: { "content-type": "application/json" },
            status: 200,
          })
        );
      }
      if (urlStr.endsWith("/api/chat/messages")) {
        persistBodies.push(JSON.parse(String(init?.body ?? "{}")));
        return Promise.resolve(
          new Response(JSON.stringify({ id: "msg-1" }), {
            headers: { "content-type": "application/json" },
            status: 200,
          })
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify(athleteContextFixture), {
          headers: { "content-type": "application/json" },
          status: 200,
        })
      );
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    await POST(
      new Request("http://localhost/api/chat", {
        body: JSON.stringify({ messages }),
        headers: { cookie: "coach_browser_session=session-token" },
        method: "POST",
      })
    );

    expect(persistBodies).toHaveLength(1);
    expect((persistBodies[0] as { id: string }).id).toBe("46db0714-d6d8-402b-a421-00b21b3a29f6");
    expect((persistBodies[0] as { parts: unknown[] }).parts).toEqual([
      { text: "Here's my chart", type: "text" },
      {
        filename: "fitness.png",
        mediaType: "image/png",
        type: "file",
        url: "https://r2.example.com/users/athlete-1/chat-attachment/2026/04/19/fitness.png",
      },
    ]);
  });

  it("still returns a stream when user-message persistence fails", async () => {
    const messages = [
      {
        id: "msg-1",
        parts: [{ text: "Hi coach", type: "text" as const }],
        role: "user" as const,
      },
    ];
    const fetchMock = vi.fn((url: RequestInfo | URL) => {
      const urlStr = String(url);
      if (urlStr.endsWith("/api/oauth/browser-token")) {
        return Promise.resolve(
          new Response(JSON.stringify({ access_token: "token-1", user_id: "athlete-1" }), {
            headers: { "content-type": "application/json" },
            status: 200,
          })
        );
      }
      if (urlStr.endsWith("/api/chat/messages")) {
        return Promise.resolve(new Response("kaboom", { status: 503 }));
      }
      return Promise.resolve(
        new Response(JSON.stringify(athleteContextFixture), {
          headers: { "content-type": "application/json" },
          status: 200,
        })
      );
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    const response = await POST(
      new Request("http://localhost/api/chat", {
        body: JSON.stringify({ messages }),
        headers: { cookie: "coach_browser_session=session-token" },
        method: "POST",
      })
    );

    expect(response.status).toBe(200);
    errorSpy.mockRestore();
  });

  it("delegates exactly one selected model window for long chat turns", async () => {
    const messages = Array.from({ length: 40 }, (_, index) => ({
      id: `message-${index}`,
      parts: [{ text: `Message ${index}`, type: "text" as const }],
      role: index % 2 === 0 ? ("user" as const) : ("assistant" as const)
    }));
    const fetchMock = vi.fn((url: RequestInfo | URL) => {
      if (String(url).endsWith("/api/oauth/browser-token")) {
        return Promise.resolve(
          new Response(JSON.stringify({ access_token: "token-1", user_id: "athlete-1" }), {
            headers: { "content-type": "application/json" },
            status: 200,
          })
        );
      }

      return Promise.resolve(
        new Response(JSON.stringify(athleteContextFixture), {
          headers: { "content-type": "application/json" },
          status: 200,
        })
      );
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    await POST(
      new Request("http://localhost/api/chat", {
        body: JSON.stringify({ messages }),
        headers: { cookie: "coach_browser_session=session-token" },
        method: "POST",
      })
    );

    expect(streamCoachTurn).toHaveBeenCalledWith(
      expect.objectContaining({
        messages: [
          expect.objectContaining({
            id: "context-window-notice",
            parts: [
              {
                text: expect.stringContaining("previous 16 chat messages"),
                type: "text",
              },
            ],
            role: "system",
          }),
          ...messages.slice(16),
        ],
        messagesAreModelSelected: true,
      })
    );
  });

  it("keeps the GPT-5 mini model in the orchestrator for chat responses", () => {
    const routeSource = readFileSync(new URL("../../lib/agent/orchestrator.ts", import.meta.url), "utf8");

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

  it("adds extracted image content to model context as text", async () => {
    const messages = [
      {
        id: "message-with-image",
        parts: [
          { text: "Here is my chart.", type: "text" as const },
          {
            filename: "fitness-chart.png",
            mediaType: "image/png",
            type: "file" as const,
            url: "https://example.com/fitness-chart.png"
          }
        ],
        role: "user" as const
      }
    ];

    const enriched = await appendImageExtractionsToMessages(messages, ({ imageUrl }) => {
      expect(imageUrl).toBe("https://example.com/fitness-chart.png");
      return Promise.resolve({
        data: {
          date_range: { end: "2026-04-26", start: "2026-04-20" },
          series: [
            { date: "2026-04-20", metric: "ctl", value: 42 },
            { date: "2026-04-21", metric: "ctl", value: 43 }
          ]
        },
        screenshot_type: "training_load_chart"
      });
    });

    expect(enriched[0]?.parts).toContainEqual({
      type: "text",
      text:
        "Extracted image content from fitness-chart.png (training_load_chart):\n" +
        JSON.stringify(
          {
            date_range: { end: "2026-04-26", start: "2026-04-20" },
            series: [
              { date: "2026-04-20", metric: "ctl", value: 42 },
              { date: "2026-04-21", metric: "ctl", value: 43 }
            ]
          },
          null,
          2
        )
    });
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
    process.env["VERCEL_AUTOMATION_BYPASS_SECRET"] = "preview-bypass";
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
        headers: {
          cookie: "coach_browser_session=session-token",
          "x-vercel-protection-bypass": "preview-bypass"
        },
        method: "POST"
      })
    );
  });

  it("returns a bounded 503 when coach streaming setup fails", async () => {
    vi.mocked(streamCoachTurn).mockRejectedValueOnce(new Error("Invalid schema for response_format"));
    const fetchMock = vi.fn((url: RequestInfo | URL) => {
      if (String(url).endsWith("/api/oauth/browser-token")) {
        return Promise.resolve(
          new Response(JSON.stringify({ access_token: "token-1", user_id: "athlete-1" }), {
            headers: { "content-type": "application/json" },
            status: 200,
          })
        );
      }

      return Promise.resolve(
        new Response(JSON.stringify(athleteContextFixture), {
          headers: { "content-type": "application/json" },
          status: 200,
        })
      );
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const response = await POST(
      new Request("http://localhost/api/chat", {
        method: "POST",
        headers: { cookie: "coach_browser_session=session-token" },
        body: JSON.stringify({ messages: [] }),
      })
    );

    expect(response.status).toBe(503);
    await expect(response.text()).resolves.toBe("Coach is unavailable right now. Please try again.");
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
          sport: "running"
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
          goal_id: "goal-1"
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
          public_url: "https://example.com/morning-run.gpx"
        }),
        method: "POST"
      })
    );
  });

});
