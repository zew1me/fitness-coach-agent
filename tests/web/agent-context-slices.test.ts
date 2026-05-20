import { describe, expect, it } from "vitest";

import { buildContextSlices } from "../../lib/agent/context-slices";

import { athleteContextFixture } from "./agent-fixtures";

describe("buildContextSlices", () => {
  it("gives intake profile, goals, and schedule without training metrics", () => {
    const slices = buildContextSlices(athleteContextFixture);

    expect(slices.intake).toEqual({
      goals: athleteContextFixture.goals,
      profile: {
        coaching_state: "onboarding",
        display_name: "Sam",
        primary_sports: ["running", "cycling"],
        weekly_available_hours: 6,
      },
      schedule: athleteContextFixture.schedule,
    });
    expect(slices.intake).not.toHaveProperty("recent_recovery");
    expect(slices.intake).not.toHaveProperty("thresholds");
    expect(slices.intake).not.toHaveProperty("current_load");
  });

  it("gives nutrition only nutrition-relevant profile fields and age", () => {
    const slices = buildContextSlices(athleteContextFixture);

    expect(slices.nutrition).toEqual({
      computed_age: 39,
      profile: {
        biological_sex: "female",
        dietary_restrictions: ["vegetarian"],
        hormone_status: "not_specified",
        nutrition_notes: "Prefers gels over real food during races",
      },
    });
    expect(slices.nutrition).not.toHaveProperty("goals");
    expect(slices.nutrition).not.toHaveProperty("thresholds");
  });

  it("gives recovery recovery logs, load, and CTL guidance without diet notes", () => {
    const slices = buildContextSlices(athleteContextFixture);

    expect(slices.recovery).toEqual({
      computed_age: 39,
      ctl_ceiling_guidance: athleteContextFixture.ctl_ceiling_guidance,
      current_load: athleteContextFixture.current_load,
      recent_recovery: athleteContextFixture.recent_recovery,
    });
    expect(slices.recovery).not.toHaveProperty("nutrition_notes");
    expect(slices.recovery).not.toHaveProperty("thresholds");
  });

  it("gives workout training plan context without nutrition details or recovery logs", () => {
    const slices = buildContextSlices(athleteContextFixture);

    expect(slices.workout).toEqual({
      active_plan: athleteContextFixture.active_plan,
      ctl_ceiling_guidance: athleteContextFixture.ctl_ceiling_guidance,
      current_load: athleteContextFixture.current_load,
      goals: athleteContextFixture.goals,
      profile: {
        primary_sports: ["running", "cycling"],
        weekly_available_hours: 6,
      },
      schedule: athleteContextFixture.schedule,
      thresholds: athleteContextFixture.thresholds,
    });
    expect(slices.workout).not.toHaveProperty("recent_recovery");
    expect(slices.workout.profile).not.toHaveProperty("dietary_restrictions");
  });
});
