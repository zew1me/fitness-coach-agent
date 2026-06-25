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

  it.each(["get_athlete_context", "save_recovery_data"])(
    "rejects proposed updates for unavailable tool %s",
    (toolName) => {
      expect(() =>
        specialistReportSchema.parse({
          confidence: "low",
          proposedUpdates: [
            {
              input: "{}",
              rationale: "Specialists must not request arbitrary tools.",
              toolName,
            },
          ],
          role: "workout",
          risks: [],
          summary: "Needs context.",
        }),
      ).toThrow();
    },
  );

  it("rejects client-supplied user_id inside proposed update inputs", () => {
    expect(() =>
      specialistReportSchema.parse({
        confidence: "high",
        proposedUpdates: [
          {
            input: JSON.stringify({
              fields: { weekly_available_hours: 5 },
              user_id: "attacker-controlled-user",
            }),
            rationale: "The server must inject identity later.",
            toolName: "update_athlete_profile",
          },
        ],
        role: "recovery",
        risks: [],
        summary: "Poor sleep.",
      }),
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
            toolName: "update_athlete_profile",
          },
        ],
        role: "recovery",
        risks: [],
        summary: "Invalid input shape.",
      }),
    ).toThrow();
  });

  it("emits an OpenAI-compatible schema for structured outputs", async () => {
    const jsonSchema = (await zodSchema(specialistReportSchema).jsonSchema) as {
      properties?: Record<
        string,
        { items?: { properties?: Record<string, { type?: unknown }> } }
      >;
      required?: string[];
    };

    expect(
      jsonSchema.properties?.["proposedUpdates"]?.items?.properties?.["input"]
        ?.type,
    ).toBe("string");
    expect(jsonSchema.required?.sort()).toEqual(
      Object.keys(jsonSchema.properties ?? {}).sort(),
    );
  });
});
