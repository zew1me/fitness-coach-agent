import { describe, expect, it } from "vitest";

import { createCoachTools } from "../../lib/agent/coach-tools";
import { coachToolDefinitions } from "../../lib/agent/tools";

describe("coachToolDefinitions", () => {
  it("exposes the planned coaching tool surface", () => {
    expect(Object.keys(coachToolDefinitions)).toEqual([
      "get_athlete_context",
      "get_recent_activities",
      "get_active_plan",
      "get_compliance_summary",
      "save_activity_from_text",
      "process_uploaded_file",
      "save_recovery_data",
      "update_schedule",
      "update_goals",
      "update_athlete_profile",
      "calculate_zones",
      "estimate_thresholds",
      "generate_training_plan",
      "adjust_plan",
      "recalibrate_thresholds"
    ]);
  });

  it("validates a goal update payload with course details", () => {
    const parsed = coachToolDefinitions.update_goals.inputSchema.parse({
      action: "create",
      goal: {
        title: "Hill climb",
        goal_type: "event",
        sport: "running",
        target_date: "2026-07-01",
        course_distance_meters: 14000,
        course_elevation_gain_meters: 700
      }
    });

    expect(parsed.goal.title).toBe("Hill climb");
    expect(parsed.goal.course_elevation_gain_meters).toBe(700);
  });

  it("routes athlete profile updates to the engine with nutrition fields", async () => {
    const requests: Array<{ body: unknown; url: string }> = [];
    const fetchImpl = (url: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      requests.push({
        body: JSON.parse(String(init?.body)),
        url: String(url),
      });

      return Promise.resolve(
        new Response(JSON.stringify({ status: "ok" }), {
          headers: { "Content-Type": "application/json" },
          status: 200,
        })
      );
    };
    const tools = createCoachTools({
      accessToken: "token",
      baseUrl: "https://coach.test",
      fetchImpl,
    });

    await (
      tools["update_athlete_profile"] as {
        execute: (input: unknown) => Promise<unknown>;
      }
    ).execute({
      fields: {
        dietary_restrictions: ["vegetarian"],
        onboarding_collected: { nutrition: true },
      },
      user_id: "ignored-client-user",
    });

    expect(requests).toEqual([
      {
        body: {
          fields: {
            dietary_restrictions: ["vegetarian"],
            onboarding_collected: { nutrition: true },
          },
        },
        url: "https://coach.test/api/engine/update-athlete-profile",
      },
    ]);
  });

  it("forwards extra internal headers to engine tool calls", async () => {
    const requests: Array<{ headers: Headers; url: string }> = [];
    const fetchImpl = (url: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      requests.push({
        headers: new Headers(init?.headers),
        url: String(url),
      });

      return Promise.resolve(
        new Response(JSON.stringify({ status: "ok" }), {
          headers: { "Content-Type": "application/json" },
          status: 200,
        })
      );
    };
    const tools = createCoachTools({
      accessToken: "token",
      baseUrl: "https://coach.test",
      extraHeaders: { "x-vercel-protection-bypass": "preview-bypass" },
      fetchImpl,
    });

    await (
      tools["get_recent_activities"] as {
        execute: (input: unknown) => Promise<unknown>;
      }
    ).execute({ limit: 1 });

    expect(requests).toHaveLength(1);
    const request = requests[0];
    if (request === undefined) {
      throw new Error("Expected one engine request.");
    }
    expect(request.url).toBe("https://coach.test/api/engine/get-recent-activities");
    expect(request.headers.get("authorization")).toBe("Bearer token");
    expect(request.headers.get("content-type")).toBe("application/json");
    expect(request.headers.get("x-vercel-protection-bypass")).toBe("preview-bypass");
  });
});
