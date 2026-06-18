import { z } from "zod";

const userInputSchema = z.object({});

const recentActivitiesInputSchema = z.object({
  limit: z.number().int().min(1).max(100).default(20),
  sport: z.string().min(1).optional(),
});

const thresholdInputSchema = z.object({
  race_distance_meters: z.number().positive().optional(),
  race_time_seconds: z.number().int().positive().optional(),
  sport: z.string().min(1),
  test_duration_minutes: z.number().int().positive().optional(),
  test_power_watts: z.number().int().positive().optional(),
});

const zonesInputSchema = z.object({
  ftp_watts: z.number().int().positive().optional(),
  lt1_hr: z.number().int().positive().optional(),
  lt1_pace_sec_km: z.number().int().positive().optional(),
  lt1_power_watts: z.number().int().positive().optional(),
  lt2_hr: z.number().int().positive().optional(),
  lt2_pace_sec_km: z.number().int().positive().optional(),
  max_hr: z.number().int().positive().optional(),
  sport: z.string().min(1),
});

const uploadedFileInputSchema = z.object({
  content_type: z.string().min(1),
  filename: z.string().min(1),
  object_key: z.string().min(1),
  public_url: z.string().url().nullable().optional(),
});

const onboardingCollectedSchema = z.object({
  nutrition: z.boolean().optional(),
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
  biological_sex: biologicalSexSchema.optional(),
  birth_date: z.string().min(1).optional(),
  coaching_state: coachingStateSchema.optional(),
  constraints: z.array(z.string().min(1)).optional(),
  dietary_restrictions: z.array(z.string().min(1)).optional(),
  display_name: z.string().min(1).optional(),
  height_cm: z.number().positive().optional(),
  hormone_status: hormoneStatusSchema.optional(),
  injuries_rehab: z.array(z.string().min(1)).optional(),
  max_hr_bpm: z.number().int().positive().optional(),
  notes: z.string().min(1).optional(),
  nutrition_notes: z.string().min(1).optional(),
  onboarding_collected: onboardingCollectedSchema.optional(),
  primary_sports: z.array(z.string().min(1)).optional(),
  resting_hr_bpm: z.number().int().positive().optional(),
  specialization_pct: z.number().int().min(0).max(100).optional(),
  weekly_available_hours: z.number().positive().optional(),
  weight_kg: z.number().positive().optional(),
});

const profileInputSchema = z.object({
  fields: profileFieldsSchema,
});

const planInputSchema = z.object({
  goal_id: z.string().min(1).optional(),
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
  process_uploaded_file: defineTool(
    "Process GPX, FIT, or screenshot uploads through the engine.",
    uploadedFileInputSchema,
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
    "Generate and persist a training plan.",
    planInputSchema,
  ),
} as const;
