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

// Canonical goal types must stay aligned with GoalType and the typed payloads in
// backend/models/training.py. The backend alone accepts legacy aliases such as `race`.
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
    // The model routinely sends `goal: null` here, which carries no goal data;
    // the engine ignores the goal for terminal actions, so only a real goal
    // object is a mistake worth rejecting.
    if (input.goal != null) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "goal must be omitted or null for complete/abandon.",
        path: ["goal"],
      });
    }
  });

const maxEffortSchema = z.object({
  duration_seconds: z.number().int().positive(),
  power_watts: z.number().positive().max(3000),
});

const thresholdInputSchema = z.object({
  efforts: z.array(maxEffortSchema).nullable(),
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

// Where the numbers came from, not who typed them. Deliberately open-ended: any device or
// app the coach can identify is a valid source. The backend normalizes it to the snake_case
// slug recovery_logs_source_check expects ("Eight Sleep" -> "eight_sleep").
const RECOVERY_SOURCE_MAX_LENGTH = 64;

// Mirrors normalize_recovery_source in backend/models/athlete.py so a value the backend would
// reject fails here, where the coach can correct it, instead of as a 422 after tool execution.
// Accents fold to ASCII first (NFKD, then drop what is left outside ASCII), so "Ōura" keeps
// its letter as "oura". The slug is checked rather than the raw text because the two can
// disagree: "心拍" has letters but no ASCII form, and folding can lengthen text past the
// limit ("ﬃ" becomes "ffi").
function recoverySourceSlug(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/[\u0080-\uffff]/g, "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

const recoverySourceSchema = z
  .string()
  .trim()
  .min(1)
  .max(RECOVERY_SOURCE_MAX_LENGTH)
  .refine((value) => recoverySourceSlug(value).length > 0, {
    message: "source must contain at least one Latin letter or digit",
  })
  .refine(
    (value) => recoverySourceSlug(value).length <= RECOVERY_SOURCE_MAX_LENGTH,
    {
      message: `source must normalize to at most ${RECOVERY_SOURCE_MAX_LENGTH} characters`,
    },
  );

const recoveryEntrySchema = z.object({
  body_battery: z.number().int().min(0).max(100).nullable(),
  hrv_ms: z.number().positive().nullable(),
  log_date: z.string().min(1).nullable(),
  notes: z.string().min(1).nullable(),
  resting_hr_bpm: z.number().int().positive().nullable(),
  sleep_consistency_pct: z.number().min(0).max(100).nullable(),
  sleep_duration_hours: z.number().nonnegative().nullable(),
  sleep_score: z.number().nonnegative().nullable(),
  source: recoverySourceSchema.nullable(),
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

const explicitPlanWorkoutSchema = z.object({
  // OpenAI strict tool schemas require every nested object field to be present.
  // Nullable targets let the coach represent a prescribed session without
  // inventing duration, distance, or TSS values the athlete never supplied.
  // Zero is a legitimate target (the coach prescribes rest days as 0 minutes),
  // so these are nonnegative like target_tss rather than strictly positive.
  description: z.string().min(1).nullable(),
  phase_name: z.string().min(1).nullable(),
  sport: z.string().min(1),
  target_distance_meters: z.number().nonnegative().nullable(),
  target_duration_minutes: z.number().int().nonnegative().nullable(),
  target_tss: z.number().nonnegative().nullable(),
  title: z.string().min(1),
  workout_date: z.iso.date(),
  workout_type: z.enum([
    "recovery",
    "endurance",
    "tempo",
    "sweet_spot",
    "threshold",
    "vo2max",
    "anaerobic",
    "sprint",
    "race",
    "strength",
    "mobility",
    "rest",
    "long_run",
    "long_ride",
    "brick",
    "interval",
    "fartlek",
    "hill_repeats",
  ]),
});

const planInputSchema = z.object({
  goal_id: z.string().min(1).optional(),
  title: z.string().min(1).optional(),
  training_model: trainingModelSchema.optional(),
  workouts: z.array(explicitPlanWorkoutSchema).min(1).max(366).optional(),
});

const adjustPlanInputSchema = z.object({
  plan_id: z.string().min(1),
  reason: z.string().min(1),
});

const resolvePlanWorkoutInputSchema = z.object({
  activity_id: z.string().min(1).nullish(),
  outcome: z.enum(["completed", "skipped"]),
  // A real plan-workout UUID, obtained from find_plan_workout or a compliance
  // summary — never a fabricated placeholder. Enforced so the coach cannot resolve
  // against a made-up id.
  plan_workout_id: z.uuid(),
});

const findPlanWorkoutInputSchema = z.object({
  // ISO date (YYYY-MM-DD) of the planned session to look up.
  workout_date: z.iso.date(),
  sport: z.string().min(1).nullish(),
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
  find_plan_workout: defineTool(
    "Look up the planned workout(s) on a given date (optionally filtered by sport) to get the real plan_workout_id. Call this before resolve_plan_workout whenever you do not already hold a concrete id — never guess or fabricate one.",
    findPlanWorkoutInputSchema,
  ),
  resolve_plan_workout: defineTool(
    "Mark a planned workout completed or skipped after the athlete confirms what happened. Optionally link the recorded activity that fulfilled it. plan_workout_id must be a real UUID from find_plan_workout or a compliance summary.",
    resolvePlanWorkoutInputSchema,
  ),
  save_activity_from_text: defineTool(
    "Persist an activity described in natural language after deterministic scoring.",
    activityTextInputSchema,
  ),
  process_uploaded_file: defineTool(
    "Process GPX, FIT, TCX, screenshot, or .zip uploads through the engine. A .zip is " +
      "unpacked server-side: contained recordings are saved to the training log " +
      "(those entries have status 'saved'), courses are analyzed but not saved " +
      "(status 'analyzed'), images are analyzed, and everything " +
      "else is discarded — do not re-save these activities yourself. A result with " +
      "status 'no_processable_files' (or an empty processed list) means the archive held " +
      "nothing usable — tell the athlete there was nothing for you in the zip. " +
      "Check the 'kind' field on every result: 'activity' is a workout the athlete " +
      "completed and it has been saved, while 'course' is a route they plan to do — " +
      "it carries terrain and pacing analysis, nothing was written, and treating it as " +
      "completed would credit them with a workout they have not done.",
    uploadedFileInputSchema,
  ),
  save_recovery_data: defineTool(
    "Persist recovery and wellness observations. Set each entry's source to where the numbers came from, not who typed them, as a short snake_case name: e.g. screenshot_extract for a wellness screenshot, or the device/app for exports and syncs (garmin_api, garmin_sidecar, intervals_sync, apple_health, whoop, oura, polar, fitbit, suunto, withings, eight_sleep, elite_hrv, ...). Any identifiable source is accepted; a pasted device export is never manual. For entries that carry only the athlete's own subjective report (energy, notes), pass null: a new day's row then defaults to manual, and a day that already holds device numbers keeps its source instead of being relabelled manual.",
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
    "Estimate LT1/LT2 thresholds from tests or races. For cycling, prefer efforts: 2+ true maximal efforts of 3-20 minutes at different durations, ideally including one of 12+ minutes, which fits critical power and W'. A rejected fit says why; relay that rather than estimating by hand.",
    thresholdInputSchema,
  ),
  generate_training_plan: defineTool(
    "Generate and persist a training plan. When saving a dated schedule already agreed with the athlete, pass title and every exact workout so their dates, sports, and intent are preserved. Omit workouts only when a generic generated plan is intended. Optional training_model: auto, longevity, performance, or recovery_return.",
    planInputSchema,
  ),
  adjust_plan: defineTool(
    "Adjust generated plan prescriptions. Bespoke exact-workout plans must instead be regenerated with their complete revised workout list.",
    adjustPlanInputSchema,
  ),
  recalibrate_thresholds: defineTool(
    "Re-estimate thresholds from recent activities, non-overwriting.",
    userInputSchema,
  ),
} as const;
