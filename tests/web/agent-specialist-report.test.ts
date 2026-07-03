import { zodSchema } from "ai";
import { describe, expect, it } from "vitest";

import {
  proposedUpdateToolShapeHints,
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

  it("rejects an empty entries array for save_recovery_data (array-level .min(1) survives relaxation)", () => {
    // Regression guard: relaxing a tool's nested object fields to optional
    // must not also drop the *array's own* length constraint. entries must
    // still contain at least one item even though each item's fields are
    // individually optional for a proposedUpdate.
    expect(() =>
      specialistReportSchema.parse({
        confidence: "medium",
        proposedUpdates: [
          {
            input: JSON.stringify({ entries: [] }),
            rationale: "No actual data provided.",
            toolName: "save_recovery_data",
          },
        ],
        role: "recovery",
        risks: [],
        summary: "Empty recovery data.",
      }),
    ).toThrow();
  });

  it("rejects a proposed update missing a required top-level field", () => {
    // Regression guard: only fields nested below the top level relax to
    // optional; a tool's own top-level required fields (e.g. `text` for
    // save_activity_from_text) must stay required.
    expect(() =>
      specialistReportSchema.parse({
        confidence: "medium",
        proposedUpdates: [
          {
            input: JSON.stringify({}),
            rationale: "Missing the required text field.",
            toolName: "save_activity_from_text",
          },
        ],
        role: "workout",
        risks: [],
        summary: "Incomplete activity proposal.",
      }),
    ).toThrow();
  });

  it("accepts a partial weekly_pattern entry for update_schedule (ZodRecord branch)", () => {
    const parsed = specialistReportSchema.parse({
      confidence: "medium",
      proposedUpdates: [
        {
          input: JSON.stringify({
            weekly_pattern: { monday: { available: true } },
          }),
          rationale: "Athlete confirmed Monday availability.",
          toolName: "update_schedule",
        },
      ],
      role: "intake",
      risks: [],
      summary: "Monday is available.",
    });

    expect(parsed.proposedUpdates[0]?.toolName).toBe("update_schedule");
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

  it("accepts a zero-param recalibrate_thresholds proposal with an empty object", () => {
    const parsed = specialistReportSchema.parse({
      confidence: "high",
      proposedUpdates: [
        {
          input: "{}",
          rationale: "Recent races suggest thresholds have shifted.",
          toolName: "recalibrate_thresholds",
        },
      ],
      role: "workout",
      risks: [],
      summary: "Recalibrate thresholds.",
    });

    expect(parsed.proposedUpdates[0]?.toolName).toBe("recalibrate_thresholds");
  });

  it("accepts a generate_training_plan proposal that omits the optional goal_id", () => {
    const parsed = specialistReportSchema.parse({
      confidence: "medium",
      proposedUpdates: [
        {
          input: "{}",
          rationale: "Athlete is ready for a structured plan.",
          toolName: "generate_training_plan",
        },
      ],
      role: "workout",
      risks: [],
      summary: "Generate a plan.",
    });

    expect(parsed.proposedUpdates[0]?.toolName).toBe("generate_training_plan");
  });

  it("rejects an adjust_plan proposal missing the required top-level reason field", () => {
    // adjust_plan has no nested objects to relax — both plan_id and reason
    // are its own top-level fields and must stay required exactly as the
    // real tool defines them.
    expect(() =>
      specialistReportSchema.parse({
        confidence: "medium",
        proposedUpdates: [
          {
            input: JSON.stringify({ plan_id: "plan-1" }),
            rationale: "Athlete asked to adjust the plan.",
            toolName: "adjust_plan",
          },
        ],
        role: "workout",
        risks: [],
        summary: "Incomplete adjust_plan proposal.",
      }),
    ).toThrow();
  });

  it("does not reject a proposed update whose input has an unrecognized key alongside valid fields", () => {
    // The relaxed per-tool schemas are plain z.object() (not .strict()), so
    // safeParse() against them ignores an extra key rather than failing —
    // this only affects whether validation blocks the proposal; the report's
    // `input` string is never rewritten from the stripped result, so the
    // extra key is still present verbatim on the returned report.
    const parsed = specialistReportSchema.parse({
      confidence: "medium",
      proposedUpdates: [
        {
          input: JSON.stringify({
            fields: { weekly_available_hours: 5 },
            unexpected_key: "not part of the tool schema",
          }),
          rationale: "Athlete confirmed reduced availability.",
          toolName: "update_athlete_profile",
        },
      ],
      role: "intake",
      risks: [],
      summary: "Reduced availability.",
    });

    expect(parsed.proposedUpdates[0]?.input).toContain("unexpected_key");
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

describe("proposedUpdateToolShapeHints", () => {
  it("describes every write tool's exact key shape, marking optional keys with '?'", () => {
    // Full-string regression guard: this constant is computed once at module
    // load from the same relaxed per-tool schemas proposedUpdateSchema
    // validates against (see orchestration-types.ts), so any drift here
    // means either a tool schema changed (expected — update this string) or
    // the shape-description logic itself broke (a real bug). Top-level
    // required fields (e.g. `text`, `plan_id`, `reason`) carry no `?`;
    // nested fields are all optional per relaxForProposedUpdate.
    expect(proposedUpdateToolShapeHints).toBe(
      "save_activity_from_text {activity_id?, text}; " +
        "save_recovery_data {entries: [{body_battery?, hrv_ms?, log_date?, notes?, resting_hr_bpm?, sleep_consistency_pct?, sleep_duration_hours?, sleep_score?, stress_score?, subjective_energy?}]}; " +
        "update_schedule {overrides?: [{available?, max_hours?, override_date?, reason?}], weekly_pattern?: {<key>: {available?, max_hours?, notes?}}}; " +
        "update_goals {action, goal?: {course_distance_meters?, course_elevation_gain_meters?, course_profile_notes?, goal_type?, improvement_baseline_value?, improvement_metric?, improvement_target_value?, sport?, target_date?, title?}, goal_id?}; " +
        "update_athlete_profile {fields: {biological_sex?, birth_date?, coaching_state?, constraints?: [...], dietary_restrictions?: [...], display_name?, height_cm?, hormone_status?, injuries_rehab?: [...], max_hr_bpm?, notes?, nutrition_notes?, onboarding_collected?: {nutrition?}, primary_sports?: [...], resting_hr_bpm?, specialization_pct?, weekly_available_hours?, weight_kg?}}; " +
        "generate_training_plan {goal_id?}; " +
        "adjust_plan {plan_id, reason}; " +
        "recalibrate_thresholds {}; " +
        "resolve_plan_workout {activity_id?, outcome, plan_workout_id}",
    );
  });

  it("lists a hint for every proposable write tool, in a semicolon-separated list", () => {
    const toolNames = [
      "save_activity_from_text",
      "save_recovery_data",
      "update_schedule",
      "update_goals",
      "update_athlete_profile",
      "generate_training_plan",
      "adjust_plan",
      "recalibrate_thresholds",
      "resolve_plan_workout",
    ];

    for (const toolName of toolNames) {
      expect(proposedUpdateToolShapeHints).toContain(toolName);
    }
    expect(proposedUpdateToolShapeHints.split("; ")).toHaveLength(
      toolNames.length,
    );
  });

  it("truncates recursion into deeply nested arrays of scalars with '...'", () => {
    // constraints/dietary_restrictions/etc. on update_athlete_profile are
    // arrays of plain strings four levels deep (fields -> array element);
    // describeShape's depth limit renders these as `[...]` rather than
    // recursing into the string primitive.
    expect(proposedUpdateToolShapeHints).toContain("constraints?: [...]");
    expect(proposedUpdateToolShapeHints).toContain(
      "dietary_restrictions?: [...]",
    );
  });
});
