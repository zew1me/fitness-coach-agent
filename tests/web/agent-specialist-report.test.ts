import { zodSchema } from "ai";
import { describe, expect, it } from "vitest";

import {
  specialistReportSchema,
  specialistReportWireSchema,
} from "../../lib/agent/orchestration-types";

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
            fields: {
              primary_sports: ["running", "cycling", "hiking"],
              weekly_available_hours: 6,
            },
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
    const parsedInput = JSON.parse(
      parsed.proposedUpdates[0]?.input ?? "{}",
    ) as Record<string, unknown>;
    const fields = parsedInput["fields"] as Record<string, unknown>;
    expect(fields["primary_sports"]).toEqual(["running", "cycling", "hiking"]);
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
      }),
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
            toolName: "save_recovery_data",
          },
        ],
        role: "recovery",
        risks: [],
        summary: "Invalid input shape.",
      }),
    ).toThrow();
  });

  it("rejects a proposed update input that doesn't match the named tool's schema", () => {
    expect(() =>
      specialistReportSchema.parse({
        confidence: "high",
        proposedUpdates: [
          {
            // Shaped for save_recovery_data, not update_goals.
            input: JSON.stringify({
              entries: [{ log_date: "2026-06-15", sleep_score: 40 }],
            }),
            rationale: "Wrong payload shape for the named tool.",
            toolName: "update_goals",
          },
        ],
        role: "workout",
        risks: [],
        summary: "Mismatched tool input.",
      }),
    ).toThrow();
  });

  it("accepts a partial patch that omits untouched fields on the named tool", () => {
    const parsed = specialistReportSchema.parse({
      confidence: "medium",
      proposedUpdates: [
        {
          // Only sleep_score is set; other recoveryEntrySchema fields are
          // omitted, mirroring what a specialist actually proposes.
          input: JSON.stringify({
            entries: [{ log_date: "2026-06-20", sleep_score: 55 }],
          }),
          rationale: "Athlete reported poor sleep last night.",
          toolName: "save_recovery_data",
        },
      ],
      role: "recovery",
      risks: [],
      summary: "Poor sleep.",
    });

    expect(parsed.proposedUpdates[0]?.toolName).toBe("save_recovery_data");
  });

  it("accepts a partial goal on an update_goals create proposal", () => {
    // The prompt hint advertises every goal subfield as optional (a
    // proposal is a preview, not the literal tool call), so a create
    // proposal with only a subset of goal fields must be accepted here —
    // full-goal completeness is enforced later by the real tool call.
    const parsed = specialistReportSchema.parse({
      confidence: "medium",
      proposedUpdates: [
        {
          input: JSON.stringify({
            action: "create",
            goal: { title: "Sub-3 marathon" },
          }),
          rationale: "Athlete stated a new race goal.",
          toolName: "update_goals",
        },
      ],
      role: "intake",
      risks: [],
      summary: "New goal.",
    });

    expect(parsed.proposedUpdates[0]?.toolName).toBe("update_goals");
  });

  it("emits an OpenAI-compatible schema for structured outputs", async () => {
    // specialistReportWireSchema is what's actually passed as an Agent's
    // outputType (see lib/agent/specialists.ts) — specialistReportSchema's
    // extra superRefine checks aren't expressible as JSON Schema and would
    // never be enforced by OpenAI's structured outputs anyway, so this must
    // assert against the wire schema, not the full one.
    const jsonSchema = (await zodSchema(specialistReportWireSchema)
      .jsonSchema) as {
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
