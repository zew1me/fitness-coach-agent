import { z } from "zod";

import { coachToolDefinitions } from "./tools";
import type { AthleteContextBundle } from "./types";

export const internalSpecialistRoleSchema = z.enum([
  "intake",
  "nutrition",
  "recovery",
  "workout",
]);
export type InternalSpecialistRole = z.infer<
  typeof internalSpecialistRoleSchema
>;

export const specialistDelegationSchema = z
  .object({
    role: internalSpecialistRoleSchema,
    objective: z.string().min(1),
    conversationDetails: z.array(z.string()),
    constraintsAndPriorDecisions: z.array(z.string()),
    unresolvedQuestions: z.array(z.string()),
    relevantCoachingMemoryIds: z.array(z.string()),
  })
  .strict();

export const delegationPlanSchema = z
  .object({ delegations: z.array(specialistDelegationSchema).max(2) })
  .strict();

export type SpecialistDelegation = z.infer<typeof specialistDelegationSchema>;
export type DelegationPlan = z.infer<typeof delegationPlanSchema>;

const proposedWriteToolNameSchema = z.enum([
  "save_activity_from_text",
  "save_recovery_data",
  "update_schedule",
  "update_goals",
  "update_athlete_profile",
  "generate_training_plan",
  "adjust_plan",
  "recalibrate_thresholds",
]);
type ProposedWriteToolName = z.infer<typeof proposedWriteToolNameSchema>;

// Nested "full state" object schemas (e.g. profile fields, goal fields,
// recovery entries) require every nullable field to be present, since the
// lead coach's actual tool call supplies a fully-merged payload. A
// specialist's proposed update is a preview of a patch, not that literal
// call, so fields nested below the top level need to become optional before
// we validate against them here — otherwise a specialist proposing "just
// update weekly_available_hours" would fail schema validation for every
// other untouched profile field.
function partializeNestedObjects(schema: z.ZodTypeAny): z.ZodTypeAny {
  if (schema instanceof z.ZodObject) {
    const newShape: Record<string, z.ZodTypeAny> = {};
    for (const [key, value] of Object.entries(schema.shape)) {
      newShape[key] = partializeNestedObjects(value as z.ZodTypeAny);
    }
    return z.object(newShape).partial();
  }
  if (schema instanceof z.ZodArray) {
    return z.array(partializeNestedObjects(schema.element as z.ZodTypeAny));
  }
  if (schema instanceof z.ZodOptional) {
    return z.optional(partializeNestedObjects(schema.unwrap() as z.ZodTypeAny));
  }
  if (schema instanceof z.ZodNullable) {
    return z.nullable(partializeNestedObjects(schema.unwrap() as z.ZodTypeAny));
  }
  if (schema instanceof z.ZodRecord) {
    return z.record(
      schema.keyType,
      partializeNestedObjects(schema.valueType as z.ZodTypeAny),
    );
  }
  return schema;
}

// The top-level fields of a tool's input schema (e.g. `text`, `plan_id`,
// `reason`) are the actual content of the proposal and stay required exactly
// as the real tool defines them; only fields nested below the top level get
// relaxed via `partializeNestedObjects`.
function relaxForProposedUpdate(schema: z.ZodTypeAny): z.ZodTypeAny {
  if (!(schema instanceof z.ZodObject)) {
    return schema;
  }
  const newShape: Record<string, z.ZodTypeAny> = {};
  for (const [key, value] of Object.entries(schema.shape)) {
    newShape[key] = partializeNestedObjects(value as z.ZodTypeAny);
  }
  return z.object(newShape);
}

// The wire format for `proposedUpdate.input` must stay a plain JSON-encoded
// string (see below), but once parsed it's validated against a relaxed
// version of the same per-tool schema the lead coach's real tool call will
// enforce, so a specialist can't propose a structurally-invalid payload for
// the tool it names.
const proposedUpdateToolInputSchemas: Record<
  ProposedWriteToolName,
  z.ZodTypeAny
> = {
  save_activity_from_text: relaxForProposedUpdate(
    coachToolDefinitions.save_activity_from_text.inputSchema,
  ),
  save_recovery_data: relaxForProposedUpdate(
    coachToolDefinitions.save_recovery_data.inputSchema,
  ),
  update_schedule: relaxForProposedUpdate(
    coachToolDefinitions.update_schedule.inputSchema,
  ),
  // update_goals's real tool schema additionally requires a *complete*
  // goal object when action is "create" (superRefine in tools.ts), since the
  // real call persists it as-is. A proposedUpdate is a preview, not that
  // literal call, so — consistent with every other tool here — only the
  // structural shape is enforced; completeness for "create" is left to the
  // lead coach's actual tool call.
  update_goals: relaxForProposedUpdate(
    coachToolDefinitions.update_goals.inputSchema,
  ),
  update_athlete_profile: relaxForProposedUpdate(
    coachToolDefinitions.update_athlete_profile.inputSchema,
  ),
  generate_training_plan: relaxForProposedUpdate(
    coachToolDefinitions.generate_training_plan.inputSchema,
  ),
  adjust_plan: relaxForProposedUpdate(
    coachToolDefinitions.adjust_plan.inputSchema,
  ),
  recalibrate_thresholds: relaxForProposedUpdate(
    coachToolDefinitions.recalibrate_thresholds.inputSchema,
  ),
};

function unwrap(schema: z.ZodTypeAny): z.ZodTypeAny {
  if (schema instanceof z.ZodOptional || schema instanceof z.ZodNullable) {
    return unwrap(schema.unwrap() as z.ZodTypeAny);
  }
  return schema;
}

function isOptionalField(schema: z.ZodTypeAny): boolean {
  return schema instanceof z.ZodOptional;
}

// Renders a shallow key map (e.g. "{fields: {display_name, ...}}") so the
// specialist prompt can show each write tool's actual top-level and
// one-level-nested key names — the wire shape validated above — instead of
// leaving the model to guess the wrapper structure.
function describeShape(schema: z.ZodTypeAny, depth: number): string {
  const unwrapped = unwrap(schema);
  if (depth <= 0) {
    return "...";
  }
  if (unwrapped instanceof z.ZodObject) {
    const entries = Object.entries(unwrapped.shape).map(
      ([key, value]) =>
        `${key}${isOptionalField(value as z.ZodTypeAny) ? "?" : ""}${
          unwrap(value as z.ZodTypeAny) instanceof z.ZodObject ||
          unwrap(value as z.ZodTypeAny) instanceof z.ZodArray ||
          unwrap(value as z.ZodTypeAny) instanceof z.ZodRecord
            ? `: ${describeShape(value as z.ZodTypeAny, depth - 1)}`
            : ""
        }`,
    );
    return `{${entries.join(", ")}}`;
  }
  if (unwrapped instanceof z.ZodArray) {
    return `[${describeShape(unwrapped.element as z.ZodTypeAny, depth - 1)}]`;
  }
  if (unwrapped instanceof z.ZodRecord) {
    return `{<key>: ${describeShape(unwrapped.valueType as z.ZodTypeAny, depth - 1)}}`;
  }
  return "";
}

// Computed once at module load and reused across every specialist prompt —
// the shapes only change when a tool schema changes.
export const proposedUpdateToolShapeHints = proposedWriteToolNameSchema.options
  .map(
    (toolName) =>
      `${toolName} ${describeShape(coachToolDefinitions[toolName].inputSchema, 3)}`,
  )
  .join("; ");

function hasUserIdKey(value: unknown): boolean {
  if (value === null || typeof value !== "object") {
    return false;
  }

  if (Array.isArray(value)) {
    return value.some(hasUserIdKey);
  }

  return Object.entries(value).some(
    ([key, nestedValue]) => key === "user_id" || hasUserIdKey(nestedValue),
  );
}

// Models using structured outputs sometimes return a raw object instead of a
// JSON-encoded string for `input`.  Preprocess coerces it to a string so the
// downstream object-level superRefine can still parse and validate it. The
// JSON Schema emitted to OpenAI remains {"type":"string"} because
// zod-to-json-schema ignores the preprocess wrapper.
const proposedUpdateInputSchema = z.preprocess(
  (val) =>
    typeof val === "object" && val !== null && !Array.isArray(val)
      ? JSON.stringify(val)
      : val,
  z.string().min(2),
);

// The OpenAI Agents SDK evaluates its own `outputType.parse()` the moment
// `result.finalOutput` is *accessed* (not when `run()` resolves), so any
// schema this feeds as `outputType` must not include our custom superRefine
// checks below — those can't be expressed as JSON Schema, so OpenAI's
// structured-output guarantee can't enforce them, and a refinement failure
// there would throw before our own repair logic (repairSpecialistReport)
// ever sees the raw data. proposedUpdateWireSchema is the structural-only
// schema safe to use as `outputType`; proposedUpdateSchema below layers the
// semantic refinements on top, applied by us afterward.
const proposedUpdateWireSchema = z
  .object({
    input: proposedUpdateInputSchema,
    rationale: z.string().min(1),
    toolName: proposedWriteToolNameSchema,
  })
  .strict();

export const proposedUpdateSchema = proposedUpdateWireSchema.superRefine(
  (update, context) => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(update.input);
    } catch {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message:
          "Specialist proposed update input must be a JSON object string.",
        path: ["input"],
      });
      return;
    }

    if (
      parsed === null ||
      typeof parsed !== "object" ||
      Array.isArray(parsed)
    ) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message:
          "Specialist proposed update input must be a JSON object string.",
        path: ["input"],
      });
      return;
    }

    if (hasUserIdKey(parsed)) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message:
          "Specialist proposed updates must not include user_id; server auth injects identity.",
        path: ["input"],
      });
      return;
    }

    const toolInputSchema = proposedUpdateToolInputSchemas[update.toolName];
    const result = toolInputSchema.safeParse(parsed);
    if (!result.success) {
      for (const issue of result.error.issues) {
        context.addIssue({ ...issue, path: ["input", ...issue.path] });
      }
    }
  },
);

// Structural-only schema — safe to pass as an Agent's `outputType`. See the
// comment on proposedUpdateWireSchema above for why the semantic refinements
// must not be included here.
export const specialistReportWireSchema = z
  .object({
    confidence: z.enum(["low", "medium", "high"]),
    proposedUpdates: z.array(proposedUpdateWireSchema),
    risks: z.array(z.string()),
    role: internalSpecialistRoleSchema,
    summary: z.string().min(1),
  })
  .strict();

export const specialistReportSchema = z
  .object({
    confidence: z.enum(["low", "medium", "high"]),
    proposedUpdates: z.array(proposedUpdateSchema),
    risks: z.array(z.string()),
    role: internalSpecialistRoleSchema,
    summary: z.string().min(1),
  })
  .strict();

export type SpecialistReport = z.infer<typeof specialistReportSchema>;

export type SpecialistReportRepairResult = {
  droppedProposedUpdateCount: number;
  report: SpecialistReport | null;
};

function extractProposedUpdatesArray(raw: unknown): unknown[] | null {
  if (
    typeof raw !== "object" ||
    raw === null ||
    !("proposedUpdates" in raw) ||
    !Array.isArray((raw as { proposedUpdates: unknown }).proposedUpdates)
  ) {
    return null;
  }
  return (raw as { proposedUpdates: unknown[] }).proposedUpdates;
}

function filterValidProposedUpdates(updates: unknown[]): {
  droppedCount: number;
  validUpdates: unknown[];
} {
  const validUpdates: unknown[] = [];
  let droppedCount = 0;
  for (const update of updates) {
    const result = proposedUpdateSchema.safeParse(update);
    if (result.success) {
      validUpdates.push(result.data);
    } else {
      droppedCount += 1;
    }
  }
  return { droppedCount, validUpdates };
}

// A specialist model occasionally proposes one malformed update (wrong tool
// shape, unparseable JSON) inside an otherwise-valid report. Rejecting the
// whole report over one bad proposedUpdate would throw away the specialist's
// summary/risks/confidence too, so this repairs the report by dropping only
// the proposedUpdates that fail validation and re-validating what remains.
// If a raw value isn't a plausible report at all, or fails validation for
// reasons other than its proposedUpdates, it's unrecoverable.
export function repairSpecialistReport(
  raw: unknown,
): SpecialistReportRepairResult {
  const direct = specialistReportSchema.safeParse(raw);
  if (direct.success) {
    return { droppedProposedUpdateCount: 0, report: direct.data };
  }

  const proposedUpdates = extractProposedUpdatesArray(raw);
  if (proposedUpdates === null) {
    return { droppedProposedUpdateCount: 0, report: null };
  }

  const { droppedCount, validUpdates } =
    filterValidProposedUpdates(proposedUpdates);
  if (droppedCount === 0) {
    // Every proposedUpdate was individually valid, so the original failure
    // came from elsewhere in the report — not something we can repair.
    return { droppedProposedUpdateCount: 0, report: null };
  }

  const repaired = specialistReportSchema.safeParse({
    ...(raw as Record<string, unknown>),
    proposedUpdates: validUpdates,
  });
  if (!repaired.success) {
    return { droppedProposedUpdateCount: 0, report: null };
  }

  return { droppedProposedUpdateCount: droppedCount, report: repaired.data };
}

type IntakeContextSlice = {
  goals: AthleteContextBundle["goals"];
  profile: {
    coaching_state: AthleteContextBundle["profile"]["coaching_state"];
    display_name: AthleteContextBundle["profile"]["display_name"] | undefined;
    primary_sports: AthleteContextBundle["profile"]["primary_sports"];
    weekly_available_hours:
      | AthleteContextBundle["profile"]["weekly_available_hours"]
      | undefined;
  };
  schedule: AthleteContextBundle["schedule"];
};

type NutritionContextSlice = {
  computed_age: AthleteContextBundle["computed_age"];
  profile: {
    biological_sex:
      | AthleteContextBundle["profile"]["biological_sex"]
      | undefined;
    dietary_restrictions:
      | AthleteContextBundle["profile"]["dietary_restrictions"]
      | undefined;
    hormone_status:
      | AthleteContextBundle["profile"]["hormone_status"]
      | undefined;
    nutrition_notes:
      | AthleteContextBundle["profile"]["nutrition_notes"]
      | undefined;
  };
};

type RecoveryContextSlice = {
  computed_age: AthleteContextBundle["computed_age"];
  ctl_ceiling_guidance: AthleteContextBundle["ctl_ceiling_guidance"];
  current_load: AthleteContextBundle["current_load"];
  recent_recovery: AthleteContextBundle["recent_recovery"];
};

type WorkoutContextSlice = {
  active_plan: AthleteContextBundle["active_plan"];
  ctl_ceiling_guidance: AthleteContextBundle["ctl_ceiling_guidance"];
  current_load: AthleteContextBundle["current_load"];
  goals: AthleteContextBundle["goals"];
  profile: {
    primary_sports: AthleteContextBundle["profile"]["primary_sports"];
    weekly_available_hours:
      | AthleteContextBundle["profile"]["weekly_available_hours"]
      | undefined;
  };
  schedule: AthleteContextBundle["schedule"];
  thresholds: AthleteContextBundle["thresholds"];
};

type LeadContextSlice = {
  active_plan: AthleteContextBundle["active_plan"];
  computed_age: AthleteContextBundle["computed_age"];
  current_load: AthleteContextBundle["current_load"];
  goals: AthleteContextBundle["goals"];
  profile: {
    coaching_state: AthleteContextBundle["profile"]["coaching_state"];
    display_name: AthleteContextBundle["profile"]["display_name"] | undefined;
    primary_sports: AthleteContextBundle["profile"]["primary_sports"];
    weekly_available_hours:
      | AthleteContextBundle["profile"]["weekly_available_hours"]
      | undefined;
  };
};

export type ContextSlices = {
  intake: IntakeContextSlice;
  lead: LeadContextSlice;
  nutrition: NutritionContextSlice;
  recovery: RecoveryContextSlice;
  workout: WorkoutContextSlice;
};
