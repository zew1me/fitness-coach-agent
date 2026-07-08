import { z } from "zod";

const userInputSchema = z.object({});

const recentActivitiesInputSchema = z.object({
  limit: z.number().int().min(1).max(100).default(20),
  sport: z.string().min(1).optional(),
});

// Nested object schemas: all fields are nullable (not optional) so that the
// generated JSON Schema includes every key in `required`. The OpenAI Responses
// API rejects tool schemas where `required` is missing or incomplete, even for
// objects nested inside arrays or additionalProperties.

const goalSchema = z.object({
  course_distance_meters: z.number().positive().nullable(),
  course_elevation_gain_meters: z.number().nonnegative().nullable(),
  course_profile_notes: z.string().min(1).nullable(),
  goal_type: z.enum([
    "event",
    "mountain",
    "improvement",
    "maintenance",
    "secondary",
  ]),
  improvement_baseline_value: z.number().nullable(),
  improvement_metric: z.string().min(1).nullable(),
  improvement_target_value: z.number().nullable(),
  sport: z.string().min(1).nullable(),
  target_date: z.string().nullable(),
  title: z.string().min(1),
});

const partialGoalSchema = goalSchema
  .partial()
  .refine(
    (goal) => Object.keys(goal).length > 0,
    "At least one goal field is required for update.",
  );

const updateGoalsInputSchema = z
  .object({
    action: z.enum(["abandon", "complete", "create", "update"]),
    goal: partialGoalSchema.nullable().optional(),
    goal_id: z.string().min(1).optional(),
  })
  .superRefine((input, context) => {
    if (input.action === "create") {
      const goalResult = goalSchema.safeParse(input.goal);
      if (!goalResult.success) {
        for (const issue of goalResult.error.issues) {
          context.addIssue({
            ...issue,
            path: ["goal", ...issue.path],
          });
        }
      }
      return;
    }

    if (input.action === "update") {
      if (!input.goal_id) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          message: "goal_id is required for update.",
          path: ["goal_id"],
        });
      }
      if (input.goal == null) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          message: "goal is required for update.",
          path: ["goal"],
        });
      }
      return;
    }

    if (!input.goal_id) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "goal_id is required for complete/abandon.",
        path: ["goal_id"],
      });
    }
    if (input.goal !== undefined) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "goal must be omitted for complete/abandon.",
        path: ["goal"],
      });
    }
  });

const thresholdInputSchema = z.object({
  race_distance_meters: z.number().positive().nullable(),
  race_time_seconds: z.number().int().positive().nullable(),
  sport: z.string().min(1),
  test_duration_minutes: z.number().int().positive().nullable(),
  test_power_watts: z.number().int().positive().nullable(),
});

const zonesInputSchema = z.object({
  ftp_watts: z.number().int().positive().nullable(),
  lt1_hr: z.number().int().positive().nullable(),
  lt1_pace_sec_km: z.number().int().positive().nullable(),
  lt1_power_watts: z.number().int().positive().nullable(),
  lt2_hr: z.number().int().positive().nullable(),
  lt2_pace_sec_km: z.number().int().positive().nullable(),
  max_hr: z.number().int().positive().nullable(),
  sport: z.string().min(1),
});

const activityTextInputSchema = z.object({
  activity_id: z.string().min(1).optional(),
  text: z.string().min(1),
});

const uploadedFileInputSchema = z.object({
  content_type: z.string().min(1),
  filename: z.string().min(1),
  object_key: z.string().min(1),
  public_url: z.string().nullable(),
});

const recoveryEntrySchema = z.object({
  body_battery: z.number().int().min(0).max(100).nullable(),
  hrv_ms: z.number().positive().nullable(),
  log_date: z.string().min(1).nullable(),
  notes: z.string().min(1).nullable(),
  resting_hr_bpm: z.number().int().positive().nullable(),
  sleep_consistency_pct: z.number().min(0).max(100).nullable(),
  sleep_duration_hours: z.number().nonnegative().nullable(),
  sleep_score: z.number().nonnegative().nullable(),
  stress_score: z.number().nonnegative().nullable(),
  subjective_energy: z.number().int().min(1).max(5).nullable(),
});

const recoveryInputSchema = z.object({
  entries: z.array(recoveryEntrySchema).min(1),
});

const weeklyPatternDaySchema = z.object({
  available: z.boolean().nullable(),
  max_hours: z.number().nonnegative().nullable(),
  notes: z.string().min(1).nullable(),
});

const scheduleOverrideSchema = z.object({
  available: z.boolean().nullable(),
  max_hours: z.number().nonnegative().nullable(),
  override_date: z.string().min(1).nullable(),
  reason: z.string().min(1).nullable(),
});

const scheduleInputSchema = z.object({
  overrides: z.array(scheduleOverrideSchema).optional(),
  weekly_pattern: z.record(z.string(), weeklyPatternDaySchema).optional(),
});

const onboardingCollectedSchema = z.object({
  nutrition: z.boolean().nullable(),
});

const biologicalSexSchema = z.enum(["male", "female", "not_specified"]);
const hormoneStatusSchema = z.enum([
  "endogenous",
  "hrt_estrogen",
  "hrt_testosterone",
  "not_specified",
]);
const coachingStateSchema = z.enum([
  "onboarding",
  "calibrating",
  "active",
  "paused",
]);

const profileFieldsSchema = z.object({
  biological_sex: biologicalSexSchema.nullable(),
  birth_date: z.string().min(1).nullable(),
  coaching_state: coachingStateSchema.nullable(),
  constraints: z.array(z.string().min(1)).nullable(),
  dietary_restrictions: z.array(z.string().min(1)).nullable(),
  display_name: z.string().min(1).nullable(),
  height_cm: z.number().positive().nullable(),
  hormone_status: hormoneStatusSchema.nullable(),
  injuries_rehab: z.array(z.string().min(1)).nullable(),
  max_hr_bpm: z.number().int().positive().nullable(),
  notes: z.string().min(1).nullable(),
  nutrition_notes: z.string().min(1).nullable(),
  onboarding_collected: onboardingCollectedSchema.nullable(),
  primary_sports: z.array(z.string().min(1)).nullable(),
  resting_hr_bpm: z.number().int().positive().nullable(),
  specialization_pct: z.number().int().min(0).max(100).nullable(),
  weekly_available_hours: z.number().positive().nullable(),
  weight_kg: z.number().positive().nullable(),
});

const profileInputSchema = z.object({
  fields: profileFieldsSchema,
});

const trainingModelSchema = z.enum([
  "auto",
  "longevity",
  "performance",
  "recovery_return",
]);

const planInputSchema = z.object({
  goal_id: z.string().min(1).optional(),
  training_model: trainingModelSchema.optional(),
});

const adjustPlanInputSchema = z.object({
  plan_id: z.string().min(1),
  reason: z.string().min(1),
});

const resolvePlanWorkoutInputSchema = z.object({
  activity_id: z.string().min(1).nullish(),
  outcome: z.enum(["completed", "skipped"]),
  plan_workout_id: z.string().min(1),
});

type ToolDefinition<TSchema extends z.ZodTypeAny> = {
  description: string;
  inputSchema: TSchema;
};

function defineTool<TSchema extends z.ZodTypeAny>(
  description: string,
  inputSchema: TSchema,
): ToolDefinition<TSchema> {
  return { description, inputSchema };
}

export const coachToolDefinitions = {
  get_athlete_context: defineTool(
    "Load full athlete context for the current turn.",
    userInputSchema,
  ),
  get_recent_activities: defineTool(
    "Load recent normalized activities.",
    recentActivitiesInputSchema,
  ),
  get_active_plan: defineTool(
    "Load the active plan and upcoming workouts.",
    userInputSchema,
  ),
  get_compliance_summary: defineTool(
    "Summarize planned versus actual workout completion since the plan started (up to 4 weeks). Auto-matches recorded activities to planned workouts, reports compliance percentage, and lists up to 3 recent unconfirmed sessions worth asking the athlete about.",
    userInputSchema,
  ),
  resolve_plan_workout: defineTool(
    "Mark a planned workout completed or skipped after the athlete confirms what happened. Optionally link the recorded activity that fulfilled it.",
    resolvePlanWorkoutInputSchema,
  ),
  save_activity_from_text: defineTool(
    "Persist an activity described in natural language after deterministic scoring.",
    activityTextInputSchema,
  ),
  process_uploaded_file: defineTool(
    "Process GPX, FIT, or screenshot uploads through the engine.",
    uploadedFileInputSchema,
  ),
  save_recovery_data: defineTool(
    "Persist recovery and wellness observations.",
    recoveryInputSchema,
  ),
  update_schedule: defineTool(
    "Persist weekly availability or date overrides.",
    scheduleInputSchema,
  ),
  update_goals: defineTool(
    "Create, update, complete, or abandon athlete goals.",
    updateGoalsInputSchema,
  ),
  update_athlete_profile: defineTool(
    "Persist extracted athlete profile fields.",
    profileInputSchema,
  ),
  calculate_zones: defineTool(
    "Calculate zone boundaries from thresholds.",
    zonesInputSchema,
  ),
  estimate_thresholds: defineTool(
    "Estimate LT1/LT2 thresholds from tests or races.",
    thresholdInputSchema,
  ),
  generate_training_plan: defineTool(
    "Generate and persist a training plan. Optional training_model: auto, longevity, performance, or recovery_return.",
    planInputSchema,
  ),
  adjust_plan: defineTool(
    "Adjust existing plan prescriptions.",
    adjustPlanInputSchema,
  ),
  recalibrate_thresholds: defineTool(
    "Re-estimate thresholds from recent activities, non-overwriting.",
    userInputSchema,
  ),
} as const;
