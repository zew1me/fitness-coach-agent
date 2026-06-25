import { describe, expect, it } from "vitest";
import { z } from "zod";

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

  it("emits OpenAI-compatible schemas for all coach tools", () => {
    for (const [name, definition] of Object.entries(coachToolDefinitions)) {
      const jsonSchema = z.toJSONSchema(definition.inputSchema);

      expect(() =>
        assertTypedAdditionalProperties(jsonSchema, name),
      ).not.toThrow();
    }
  });

  it("validates profile onboarding domain field names", () => {
    const fullProfileFields = {
      biological_sex: "not_specified" as const,
      birth_date: null,
      coaching_state: null,
      constraints: null,
      dietary_restrictions: ["vegetarian"],
      display_name: null,
      height_cm: null,
      hormone_status: "not_specified" as const,
      injuries_rehab: null,
      max_hr_bpm: null,
      notes: null,
      nutrition_notes: null,
      onboarding_collected: { nutrition: true },
      primary_sports: null,
      resting_hr_bpm: null,
      specialization_pct: null,
      weekly_available_hours: null,
      weight_kg: null,
    };
    expect(
      coachToolDefinitions.update_athlete_profile.inputSchema.parse({
        fields: fullProfileFields,
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
        fields: { ...fullProfileFields, hormone_status: "not_provided" },
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

  it("returns a graceful result for unsupported uploaded file types", async () => {
    let fetchCalled = false;
    const fetchImpl = (): Promise<Response> => {
      fetchCalled = true;
      return Promise.resolve(new Response(null, { status: 200 }));
    };
    const tools = createCoachTools({
      accessToken: "token",
      baseUrl: "https://coach.test",
      fetchImpl,
    });

    const execute = (
      tools["process_uploaded_file"] as {
        execute: (input: unknown) => Promise<unknown>;
      }
    ).execute;

    const result = await execute({
      content_type: "application/pdf",
      filename: "plan.pdf",
      object_key: "uploads/plan.pdf",
      public_url: "https://files.test/plan.pdf",
    });

    expect(fetchCalled).toBe(false);
    expect(result).toMatchObject({
      status: "unsupported_file_type",
      tool: "process_uploaded_file",
    });
  });
});
