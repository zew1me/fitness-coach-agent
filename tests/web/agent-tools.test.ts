import { describe, expect, it } from "vitest";

import { coachToolDefinitions } from "../../lib/agent/tools";

describe("coachToolDefinitions", () => {
  it("exposes the planned coaching tool surface", () => {
    expect(Object.keys(coachToolDefinitions)).toEqual([
      "get_athlete_context",
      "get_recent_activities",
      "get_active_plan",
      "get_compliance_summary",
      "save_activity_from_text",
      "process_uploaded_file",
      "save_recovery_data",
      "update_schedule",
      "update_goals",
      "update_athlete_profile",
      "calculate_zones",
      "estimate_thresholds",
      "generate_training_plan",
      "adjust_plan",
      "recalibrate_thresholds",
      "web_search"
    ]);
  });

  it("validates a goal update payload with course details", () => {
    const parsed = coachToolDefinitions.update_goals.inputSchema.parse({
      action: "create",
      goal: {
        title: "Hill climb",
        goal_type: "event",
        sport: "running",
        target_date: "2026-07-01",
        course_distance_meters: 14000,
        course_elevation_gain_meters: 700
      }
    });

    expect(parsed.goal.title).toBe("Hill climb");
    expect(parsed.goal.course_elevation_gain_meters).toBe(700);
  });
});
