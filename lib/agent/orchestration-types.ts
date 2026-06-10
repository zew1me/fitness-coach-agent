import { z } from "zod";

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

export type TurnIntentKind =
  | "general"
  | "intake"
  | "mixed"
  | "nutrition"
  | "plan_change"
  | "recovery"
  | "workout";

export type TurnIntent = {
  kind: TurnIntentKind;
  specialists: InternalSpecialistRole[];
};

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

const proposedUpdateInputSchema = z
  .string()
  .min(2)
  .superRefine((input, context) => {
    try {
      const parsed = JSON.parse(input) as unknown;
      if (
        parsed === null ||
        typeof parsed !== "object" ||
        Array.isArray(parsed)
      ) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          message:
            "Specialist proposed update input must be a JSON object string.",
        });
        return;
      }
      if (!hasUserIdKey(parsed)) {
        return;
      }
    } catch {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message:
          "Specialist proposed update input must be a JSON object string.",
      });
      return;
    }

    context.addIssue({
      code: z.ZodIssueCode.custom,
      message:
        "Specialist proposed updates must not include user_id; server auth injects identity.",
    });
  });

export const proposedUpdateSchema = z
  .object({
    input: proposedUpdateInputSchema,
    rationale: z.string().min(1),
    toolName: proposedWriteToolNameSchema,
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

export const specialistReportsSchema = z.array(specialistReportSchema);

export type SpecialistReport = z.infer<typeof specialistReportSchema>;

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
