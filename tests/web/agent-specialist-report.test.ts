import { zodSchema } from "ai";
import { describe, expect, it } from "vitest";

import { specialistReportSchema } from "../../lib/agent/orchestration-types";

describe("specialistReportSchema", () => {
  it("accepts a valid specialist report with approved proposed updates", () => {
    const parsed = specialistReportSchema.parse({
      confidence: "medium",
      proposedUpdates: [
        {
          input: JSON.stringify({
            fields: {
              weekly_available_hours: 7,
            },
          }),
          rationale: "The athlete gave a new weekly availability.",
          toolName: "update_athlete_profile",
        },
      ],
      role: "intake",
      risks: ["Confirm this is a typical week."],
      summary: "Athlete can train seven hours per week.",
    });

    expect(parsed.proposedUpdates[0]?.toolName).toBe("update_athlete_profile");
  });

  it("accepts a raw object for input — coerces it to a JSON string (model regression guard)", () => {
    // The OpenAI model sometimes returns a raw object for `input` instead of a
    // JSON-encoded string.  The preprocess coercion must accept this and serialise
    // it so the downstream validation still runs correctly.
    const parsed = specialistReportSchema.parse({
      confidence: "high",
      proposedUpdates: [
        {
          input: {
            primary_sports: ["running", "cycling", "hiking"],
            weekly_available_hours: 6,
          },
          rationale: "Multi-sport athlete with 6h/week availability.",
          toolName: "update_athlete_profile",
        },
      ],
      role: "intake",
      risks: [],
      summary: "Multi-sport athlete, 6h/week.",
    });

    expect(parsed.proposedUpdates[0]?.toolName).toBe("update_athlete_profile");
    // After coercion the stored value is the JSON string, not the raw object.
    expect(typeof parsed.proposedUpdates[0]?.input).toBe("string");
    const parsedInput = JSON.parse(parsed.proposedUpdates[0]?.input ?? "{}") as Record<string, unknown>;
    expect(parsedInput["primary_sports"]).toEqual(["running", "cycling", "hiking"]);
  });

  it("rejects a raw object with nested user_id — tests preprocessing path", () => {
    // When the model returns a raw object (triggering preprocess coercion), the
    // user_id guard must still catch nested user_id fields after serialization.
    const result = specialistReportSchema.safeParse({
      confidence: "medium",
      proposedUpdates: [
        {
          input: {
            entries: [{ log_date: "2026-06-15", sleep_score: 35 }],
            user_id: "malicious-user-id",
          },
          rationale: "Server must inject identity after coercion.",
          toolName: "save_recovery_data",
        },
      ],
      role: "recovery",
      risks: [],
      summary: "Poor sleep data.",
    });

    expect(result.success).toBe(false);
  });

  it("rejects proposed updates for unknown or read-only tools", () => {
    expect(() =>
      specialistReportSchema.parse({
        confidence: "low",
        proposedUpdates: [
          {
            input: "{}",
            rationale: "Specialists must not request arbitrary tools.",
            toolName: "get_athlete_context",
          },
        ],
        role: "workout",
        risks: [],
        summary: "Needs context.",
      })
    ).toThrow();
  });

  it("rejects client-supplied user_id inside proposed update inputs", () => {
    expect(() =>
      specialistReportSchema.parse({
        confidence: "high",
        proposedUpdates: [
          {
            input: JSON.stringify({
              entries: [{ log_date: "2026-05-04", sleep_score: 40 }],
              user_id: "attacker-controlled-user",
            }),
            rationale: "The server must inject identity later.",
            toolName: "save_recovery_data",
          },
        ],
        role: "recovery",
        risks: [],
        summary: "Poor sleep.",
      })
    ).toThrow();
  });

  it("rejects proposed update inputs that are not JSON object strings", () => {
    expect(() =>
      specialistReportSchema.parse({
        confidence: "medium",
        proposedUpdates: [
          {
            input: JSON.stringify(["not", "an", "object"]),
            rationale: "Arrays are not valid tool inputs.",
            toolName: "save_recovery_data",
          },
        ],
        role: "recovery",
        risks: [],
        summary: "Invalid input shape.",
      })
    ).toThrow();
  });

  it("emits an OpenAI-compatible schema for structured outputs", async () => {
    const jsonSchema = (await zodSchema(specialistReportSchema).jsonSchema) as {
      properties?: Record<string, { items?: { properties?: Record<string, { type?: unknown }> } }>;
      required?: string[];
    };

    expect(
      jsonSchema.properties?.["proposedUpdates"]?.items?.properties?.["input"]?.type
    ).toBe("string");
    expect(jsonSchema.required?.sort()).toEqual(
      Object.keys(jsonSchema.properties ?? {}).sort()
    );
  });
});
