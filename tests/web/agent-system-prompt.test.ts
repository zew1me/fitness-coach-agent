import { describe, expect, it } from "vitest";

import { buildCoachSystemPrompt } from "../../lib/agent/system-prompt";
import type { AthleteContextBundle } from "../../lib/agent/types";

const context: AthleteContextBundle = {
  profile: {
    user_id: "athlete-1",
    display_name: "Sam",
    primary_sports: ["running", "cycling"],
    coaching_state: "onboarding",
    weekly_available_hours: 6
  },
  computed_age: 39,
  thresholds: [],
  goals: [
    {
      id: "goal-1",
      user_id: "athlete-1",
      goal_type: "event",
      sport: "running",
      title: "14km hill climb",
      target_date: "2026-07-01",
      course_distance_meters: 14000,
      course_elevation_gain_meters: 700,
      priority: 1,
      status: "active"
    }
  ],
  current_load: {
    user_id: "athlete-1",
    snapshot_date: "2026-04-01",
    daily_tss: 60,
    ctl: 42,
    atl: 50,
    tsb: -8
  },
  recent_recovery: [],
  schedule: null,
  active_plan: null,
  ctl_ceiling_guidance: {
    age_bracket: "30-39",
    elite_ctl: 130,
    committed_amateur_ctl: 85,
    recreational_ctl: 45,
    recovery_week_frequency: "every 3-4 weeks",
    notes: "Recovery starts to matter more."
  }
};

describe("buildCoachSystemPrompt", () => {
  it("includes coaching philosophy, athlete context, and onboarding instructions", () => {
    const prompt = buildCoachSystemPrompt(context);

    expect(prompt).toContain("Seiler");
    expect(prompt).toContain("onboarding");
    expect(prompt).toContain("14km hill climb");
    expect(prompt).toContain("CTL 42");
    expect(prompt).toContain("30-39");
    expect(prompt).toContain("extract multiple fields");
  });
});
