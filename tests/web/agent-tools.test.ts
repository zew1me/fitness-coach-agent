import { zodSchema } from "ai";
import { describe, expect, it } from "vitest";

import { createCoachTools } from "../../lib/agent/coach-tools";
import { coachToolDefinitions } from "../../lib/agent/tools";

function assertTypedAdditionalProperties(schema: unknown, path = "$"): void {
  if (schema === null || typeof schema !== "object") {
    return;
  }

  if (Array.isArray(schema)) {
    schema.forEach((item, index) =>
      assertTypedAdditionalProperties(item, `${path}[${index}]`),
    );
    return;
  }

  const record = schema as Record<string, unknown>;
  const additionalProperties = record["additionalProperties"];
  if (
    additionalProperties !== undefined &&
    additionalProperties !== false &&
    additionalProperties !== true
  ) {
    expect(additionalProperties, `${path}.additionalProperties`).toEqual(
      expect.objectContaining({ type: expect.anything() }),
    );
  }

  for (const [key, value] of Object.entries(record)) {
    assertTypedAdditionalProperties(value, `${path}.${key}`);
  }
}

describe("coachToolDefinitions", () => {
  it("exposes only coach tools with real execution paths", () => {
    expect(Object.keys(coachToolDefinitions)).toEqual([
      "get_athlete_context",
      "get_recent_activities",
      "get_active_plan",
      "process_uploaded_file",
      "update_athlete_profile",
      "calculate_zones",
      "estimate_thresholds",
      "generate_training_plan",
    ]);
    expect(Object.keys(coachToolDefinitions)).not.toEqual(
      expect.arrayContaining([
        "get_compliance_summary",
        "save_activity_from_text",
        "save_recovery_data",
        "update_schedule",
        "update_goals",
        "adjust_plan",
        "recalibrate_thresholds",
      ]),
    );
  });

  it("emits OpenAI-compatible schemas for all coach tools", async () => {
    for (const [name, definition] of Object.entries(coachToolDefinitions)) {
      const jsonSchema = await zodSchema(definition.inputSchema).jsonSchema;

      expect(() =>
        assertTypedAdditionalProperties(jsonSchema, name),
      ).not.toThrow();
    }
  });

  it("validates profile onboarding domain field names", () => {
    expect(
      coachToolDefinitions.update_athlete_profile.inputSchema.parse({
        fields: {
          biological_sex: "not_specified",
          dietary_restrictions: ["vegetarian"],
          hormone_status: "not_specified",
          onboarding_collected: { nutrition: true },
        },
      }),
    ).toMatchObject({
      fields: {
        biological_sex: "not_specified",
        hormone_status: "not_specified",
        onboarding_collected: { nutrition: true },
      },
    });

    expect(() =>
      coachToolDefinitions.update_athlete_profile.inputSchema.parse({
        fields: {
          hormone_status: "not_provided",
        },
      }),
    ).toThrow();
  });

  it("routes athlete profile updates to the engine with nutrition fields", async () => {
    const requests: Array<{ body: unknown; url: string }> = [];
    const fetchImpl = (
      url: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      requests.push({
        body: JSON.parse(String(init?.body)),
        url: String(url),
      });

      return Promise.resolve(
        new Response(JSON.stringify({ status: "ok" }), {
          headers: { "Content-Type": "application/json" },
          status: 200,
        }),
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
    const fetchImpl = (
      url: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      requests.push({
        headers: new Headers(init?.headers),
        url: String(url),
      });

      return Promise.resolve(
        new Response(JSON.stringify({ status: "ok" }), {
          headers: { "Content-Type": "application/json" },
          status: 200,
        }),
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
    expect(request.url).toBe(
      "https://coach.test/api/engine/get-recent-activities",
    );
    expect(request.headers.get("authorization")).toBe("Bearer token");
    expect(request.headers.get("content-type")).toBe("application/json");
    expect(request.headers.get("x-vercel-protection-bypass")).toBe(
      "preview-bypass",
    );
  });
});
