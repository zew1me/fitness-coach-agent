import { z } from "zod";

const userInputSchema = z.object({
  user_id: z.string().min(1)
});

const recentActivitiesInputSchema = userInputSchema.extend({
  limit: z.number().int().min(1).max(100).default(20),
  sport: z.string().min(1).optional()
});

const goalSchema = z.object({
  course_distance_meters: z.number().positive().optional(),
  course_elevation_gain_meters: z.number().nonnegative().optional(),
  course_profile: z.record(z.unknown()).optional(),
  goal_type: z.string().min(1),
  improvement_baseline_value: z.number().optional(),
  improvement_metric: z.string().min(1).optional(),
  improvement_target_value: z.number().optional(),
  sport: z.string().min(1).optional(),
  target_date: z.string().optional(),
  title: z.string().min(1)
});

const thresholdInputSchema = z.object({
  race_distance_meters: z.number().positive().optional(),
  race_time_seconds: z.number().int().positive().optional(),
  sport: z.string().min(1),
  test_duration_minutes: z.number().int().positive().optional(),
  test_power_watts: z.number().int().positive().optional(),
  user_id: z.string().min(1)
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
  user_id: z.string().min(1)
});

const activityTextInputSchema = z.object({
  text: z.string().min(1),
  user_id: z.string().min(1)
});

const uploadedFileInputSchema = z.object({
  content_type: z.string().min(1),
  filename: z.string().min(1),
  object_key: z.string().min(1),
  public_url: z.string().url().nullable().optional(),
  user_id: z.string().min(1)
});

const recoveryInputSchema = z.object({
  entries: z.array(z.record(z.unknown())).min(1),
  user_id: z.string().min(1)
});

const scheduleInputSchema = z.object({
  overrides: z.array(z.record(z.unknown())).optional(),
  user_id: z.string().min(1),
  weekly_pattern: z.record(z.unknown()).optional()
});

const profileInputSchema = z.object({
  fields: z.record(z.unknown()),
  user_id: z.string().min(1)
});

const planInputSchema = userInputSchema.extend({
  goal_id: z.string().min(1).optional()
});

const adjustPlanInputSchema = z.object({
  plan_id: z.string().min(1),
  reason: z.string().min(1),
  user_id: z.string().min(1)
});

type ToolDefinition<TSchema extends z.ZodTypeAny> = {
  description: string;
  inputSchema: TSchema;
};

function defineTool<TSchema extends z.ZodTypeAny>(
  description: string,
  inputSchema: TSchema
): ToolDefinition<TSchema> {
  return { description, inputSchema };
}

export const coachToolDefinitions = {
  get_athlete_context: defineTool("Load full athlete context for the current turn.", userInputSchema),
  get_recent_activities: defineTool("Load recent normalized activities.", recentActivitiesInputSchema),
  get_active_plan: defineTool("Load the active plan and upcoming workouts.", userInputSchema),
  get_compliance_summary: defineTool(
    "Summarize planned versus actual completion over recent weeks.",
    userInputSchema
  ),
  save_activity_from_text: defineTool(
    "Persist an activity described in natural language after deterministic scoring.",
    activityTextInputSchema
  ),
  process_uploaded_file: defineTool(
    "Process GPX, FIT, or screenshot uploads through the engine.",
    uploadedFileInputSchema
  ),
  save_recovery_data: defineTool("Persist recovery and wellness observations.", recoveryInputSchema),
  update_schedule: defineTool("Persist weekly availability or date overrides.", scheduleInputSchema),
  update_goals: defineTool(
    "Create, update, complete, or abandon athlete goals.",
    z.object({
      action: z.enum(["abandon", "complete", "create", "update"]),
      goal: goalSchema,
      goal_id: z.string().min(1).optional(),
      user_id: z.string().min(1).optional()
    })
  ),
  update_athlete_profile: defineTool("Persist extracted athlete profile fields.", profileInputSchema),
  calculate_zones: defineTool("Calculate zone boundaries from thresholds.", zonesInputSchema),
  estimate_thresholds: defineTool("Estimate LT1/LT2 thresholds from tests or races.", thresholdInputSchema),
  generate_training_plan: defineTool("Generate and persist a training plan.", planInputSchema),
  adjust_plan: defineTool("Adjust existing plan prescriptions.", adjustPlanInputSchema),
  recalibrate_thresholds: defineTool(
    "Re-estimate thresholds from recent performance evidence.",
    userInputSchema
  )
} as const;

export type CoachToolName = keyof typeof coachToolDefinitions;
