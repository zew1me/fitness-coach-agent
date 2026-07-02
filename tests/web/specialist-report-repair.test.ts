import { describe, expect, it } from "vitest";

import {
  repairSpecialistReport,
  specialistReportSchema,
  specialistReportWireSchema,
} from "../../lib/agent/orchestration-types";

describe("repairSpecialistReport", () => {
  it("returns the report unchanged when everything is valid", () => {
    const raw = {
      confidence: "high",
      proposedUpdates: [
        {
          input: JSON.stringify({ plan_id: "plan-1", reason: "Sick day" }),
          rationale: "Athlete is sick.",
          toolName: "adjust_plan",
        },
      ],
      risks: [],
      role: "workout",
      summary: "Adjust the plan.",
    };

    const result = repairSpecialistReport(raw);

    expect(result.report?.proposedUpdates).toHaveLength(1);
    expect(result.droppedProposedUpdateCount).toBe(0);
  });

  it("drops only the proposedUpdate that fails schema validation, keeping the report", () => {
    const raw = {
      confidence: "low",
      proposedUpdates: [
        {
          // Natural language input — fails JSON.parse, cannot repair.
          input: "recalibrate the thresholds for me",
          rationale: "Athlete asked to recalibrate.",
          toolName: "recalibrate_thresholds",
        },
        {
          input: JSON.stringify({ plan_id: "plan-1", reason: "Sick day" }),
          rationale: "Also adjust the plan.",
          toolName: "adjust_plan",
        },
      ],
      risks: [],
      role: "workout",
      summary: "Recalibrate thresholds and adjust the plan.",
    };

    const result = repairSpecialistReport(raw);

    expect(result.report).not.toBeNull();
    expect(result.report?.summary).toBe(
      "Recalibrate thresholds and adjust the plan.",
    );
    expect(result.report?.proposedUpdates).toHaveLength(1);
    expect(result.report?.proposedUpdates[0]?.toolName).toBe("adjust_plan");
    expect(result.droppedProposedUpdateCount).toBe(1);
  });

  it("drops the whole report when a non-proposedUpdates field is invalid", () => {
    const raw = {
      confidence: "low",
      proposedUpdates: [],
      risks: [],
      role: "not-a-real-role",
      summary: "",
    };

    const result = repairSpecialistReport(raw);

    expect(result.report).toBeNull();
    expect(result.droppedProposedUpdateCount).toBe(0);
  });

  it("drops the whole report when it is not a plausible object at all", () => {
    const result = repairSpecialistReport("not an object");

    expect(result.report).toBeNull();
    expect(result.droppedProposedUpdateCount).toBe(0);
  });
});

describe("specialistReportWireSchema", () => {
  it("accepts reports that fail the full schema's semantic refinements", () => {
    // This is the exact contract repairSpecialistReport depends on being
    // live in production: the OpenAI Agents SDK validates finalOutput
    // against `outputType` (specialistReportWireSchema) the moment it's
    // accessed. If the wire schema rejected the same things the full schema
    // does, the SDK would throw before repairSpecialistReport ever saw the
    // raw report, and its per-proposedUpdate repair would never run.
    const raw = {
      confidence: "low",
      proposedUpdates: [
        {
          // Natural language, not a JSON object string — fails the full
          // schema's superRefine, but is still a plain non-empty string, so
          // it passes the wire schema's structural check.
          input: "recalibrate the thresholds for me",
          rationale: "Athlete asked to recalibrate.",
          toolName: "recalibrate_thresholds",
        },
      ],
      risks: [],
      role: "workout",
      summary: "Recalibrate thresholds.",
    };

    expect(specialistReportWireSchema.safeParse(raw).success).toBe(true);
    expect(specialistReportSchema.safeParse(raw).success).toBe(false);
  });
});
