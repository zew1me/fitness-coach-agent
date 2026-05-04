import type { AthleteContextBundle } from "../../lib/agent/types";

export const athleteContextFixture: AthleteContextBundle = {
  active_plan: {
    end_date: "2026-06-14",
    phases: [{ name: "Base", weeks: 4 }],
    start_date: "2026-05-04",
    status: "active",
    title: "Hill climb build",
  },
  computed_age: 39,
  ctl_ceiling_guidance: {
    age_bracket: "30-39",
    committed_amateur_ctl: 85,
    elite_ctl: 130,
    notes: "Recovery starts to matter more.",
    recovery_week_frequency: "every 3-4 weeks",
    recreational_ctl: 45,
  },
  current_load: {
    atl: 50,
    ctl: 42,
    daily_tss: 60,
    snapshot_date: "2026-04-01",
    tsb: -8,
    user_id: "athlete-1",
  },
  goals: [
    {
      course_distance_meters: 14000,
      course_elevation_gain_meters: 700,
      goal_type: "event",
      id: "goal-1",
      priority: 1,
      sport: "running",
      status: "active",
      target_date: "2026-07-01",
      title: "14km hill climb",
      user_id: "athlete-1",
    },
  ],
  profile: {
    biological_sex: "female",
    coaching_state: "onboarding",
    dietary_restrictions: ["vegetarian"],
    display_name: "Sam",
    hormone_status: "not_specified",
    nutrition_notes: "Prefers gels over real food during races",
    primary_sports: ["running", "cycling"],
    user_id: "athlete-1",
    weekly_available_hours: 6,
  },
  recent_recovery: [
    {
      body_battery: 55,
      hrv_ms: 48,
      log_date: "2026-05-03",
      resting_hr_bpm: 54,
      sleep_score: 72,
    },
  ],
  schedule: {
    weekly_pattern: {
      monday: "rest",
      saturday: "long",
    },
  },
  thresholds: [
    {
      confidence: "estimated",
      effective_from: "2026-01-01",
      lt2_hr_bpm: 172,
      sport: "running",
    },
  ],
};
