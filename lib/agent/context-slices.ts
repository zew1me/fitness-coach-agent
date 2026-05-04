import type { ContextSlices } from "./orchestration-types";
import type { AthleteContextBundle } from "./types";

export function buildContextSlices(context: AthleteContextBundle): ContextSlices {
  return {
    intake: {
      goals: context.goals,
      profile: {
        coaching_state: context.profile.coaching_state,
        display_name: context.profile.display_name,
        primary_sports: context.profile.primary_sports,
        weekly_available_hours: context.profile.weekly_available_hours,
      },
      schedule: context.schedule,
    },
    lead: {
      active_plan: context.active_plan,
      computed_age: context.computed_age,
      current_load: context.current_load,
      goals: context.goals,
      profile: {
        coaching_state: context.profile.coaching_state,
        display_name: context.profile.display_name,
        primary_sports: context.profile.primary_sports,
        weekly_available_hours: context.profile.weekly_available_hours,
      },
    },
    nutrition: {
      computed_age: context.computed_age,
      profile: {
        biological_sex: context.profile.biological_sex,
        dietary_restrictions: context.profile.dietary_restrictions,
        hormone_status: context.profile.hormone_status,
        nutrition_notes: context.profile.nutrition_notes,
      },
    },
    recovery: {
      computed_age: context.computed_age,
      ctl_ceiling_guidance: context.ctl_ceiling_guidance,
      current_load: context.current_load,
      recent_recovery: context.recent_recovery,
    },
    workout: {
      active_plan: context.active_plan,
      ctl_ceiling_guidance: context.ctl_ceiling_guidance,
      current_load: context.current_load,
      goals: context.goals,
      profile: {
        primary_sports: context.profile.primary_sports,
        weekly_available_hours: context.profile.weekly_available_hours,
      },
      schedule: context.schedule,
      thresholds: context.thresholds,
    },
  };
}
