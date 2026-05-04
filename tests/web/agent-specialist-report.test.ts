import { describe, expect, it } from "vitest";

import { specialistReportSchema } from "../../lib/agent/orchestration-types";

describe("specialistReportSchema", () => {
  it("accepts a valid specialist report with approved proposed updates", () => {
    const parsed = specialistReportSchema.parse({
      confidence: "medium",
      proposedUpdates: [
        {
          input: {
            fields: {
              weekly_available_hours: 7,
            },
          },
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

  it("rejects proposed updates for unknown or read-only tools", () => {
    expect(() =>
      specialistReportSchema.parse({
        confidence: "low",
        proposedUpdates: [
          {
            input: {},
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
            input: {
              entries: [{ log_date: "2026-05-04", sleep_score: 40 }],
              user_id: "attacker-controlled-user",
            },
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
});
