import { describe, expect, it } from "vitest";

import {
  addDays,
  buildCalendarWeeks,
  CALENDAR_FUTURE_DAYS,
  CALENDAR_PAST_DAYS,
  calendarDateRange,
  derivedWorkoutStatus,
  groupCalendarItemsByDay,
  monthLabel,
} from "../../lib/calendar-view";
import {
  calendarPlannedWorkoutSchema,
  calendarResponseSchema,
} from "../../lib/schemas";
import type { CalendarPlannedWorkout } from "../../lib/schemas";

describe("calendar window", () => {
  it("spans the past 42 days and upcoming 8 weeks, aligned to Monday weeks", () => {
    expect(CALENDAR_PAST_DAYS).toBe(42);
    expect(CALENDAR_FUTURE_DAYS).toBe(56);

    // 2026-07-03 is a Friday: raw window is 2026-05-22 → 2026-08-28,
    // which aligns out to Monday 2026-05-18 → Sunday 2026-08-30.
    const range = calendarDateRange("2026-07-03");
    expect(range).toEqual({ start: "2026-05-18", end: "2026-08-30" });
  });

  it("adds days across month and year boundaries without timezone drift", () => {
    expect(addDays("2026-05-31", 1)).toBe("2026-06-01");
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addDays("2026-03-29", 1)).toBe("2026-03-30"); // DST-change day in Europe
    expect(addDays("2026-07-03", -42)).toBe("2026-05-22");
  });

  it("builds full Monday-to-Sunday weeks covering the range", () => {
    const weeks = buildCalendarWeeks("2026-05-18", "2026-08-30");
    expect(weeks).toHaveLength(15);
    for (const week of weeks) {
      expect(week).toHaveLength(7);
    }
    expect(weeks[0]?.[0]).toBe("2026-05-18");
    expect(weeks[0]?.[6]).toBe("2026-05-24");
    expect(weeks[14]?.[6]).toBe("2026-08-30");
  });

  it("labels months for grid headings", () => {
    expect(monthLabel("2026-07-01")).toBe("July 2026");
  });
});

describe("calendar response parsing and grouping", () => {
  const payload = {
    start: "2026-05-18",
    end: "2026-08-30",
    planned_workouts: [
      {
        id: "workout-1",
        plan_id: "plan-1",
        user_id: "athlete-1",
        workout_date: "2026-07-04",
        day_of_week: 5,
        week_number: 2,
        phase_name: "Build",
        sport: "cycling",
        title: "Sweet spot 3x12",
        description: "3x12min at 90% FTP",
        workout_type: "sweet_spot",
        target_duration_minutes: 75,
        target_tss: 80,
        status: "scheduled",
        actual_activity_id: null,
        unknown_future_field: true,
      },
      {
        id: "workout-2",
        plan_id: "plan-1",
        user_id: "athlete-1",
        workout_date: "2026-07-04",
        day_of_week: 5,
        week_number: 2,
        sport: "running",
        title: "Easy shakeout",
        workout_type: "recovery",
        status: "completed",
      },
    ],
    activities: [
      {
        id: "activity-1",
        user_id: "athlete-1",
        sport: "running",
        activity_date: "2026-06-20",
        started_at: "2026-06-20T06:30:00Z",
        duration_seconds: 3600,
        distance_meters: 12000,
        tss: 68,
        rpe: 6,
        athlete_notes: "Felt strong on the hills.",
      },
    ],
  };

  it("parses the backend payload, tolerating unknown fields", () => {
    const parsed = calendarResponseSchema.parse(payload);
    expect(parsed.planned_workouts).toHaveLength(2);
    expect(parsed.activities[0]?.tss).toBe(68);
  });

  it("groups planned workouts and activities under their day", () => {
    const parsed = calendarResponseSchema.parse(payload);
    const byDay = groupCalendarItemsByDay(parsed);

    const fourth = byDay.get("2026-07-04");
    expect(fourth?.planned.map((w) => w.id)).toEqual([
      "workout-1",
      "workout-2",
    ]);
    expect(fourth?.activities).toEqual([]);

    const twentieth = byDay.get("2026-06-20");
    expect(twentieth?.planned).toEqual([]);
    expect(twentieth?.activities[0]?.id).toBe("activity-1");

    expect(byDay.has("2026-07-05")).toBe(false);
  });
});

describe("derivedWorkoutStatus", () => {
  const workout = (
    status: string,
    workoutDate: string,
    workoutType = "endurance",
  ): CalendarPlannedWorkout =>
    calendarPlannedWorkoutSchema.parse({
      id: "workout-1",
      plan_id: "plan-1",
      workout_date: workoutDate,
      sport: "cycling",
      title: "Endurance ride",
      workout_type: workoutType,
      status,
    });

  it("derives unconfirmed for a past workout still scheduled", () => {
    expect(
      derivedWorkoutStatus(workout("scheduled", "2026-07-01"), "2026-07-03"),
    ).toBe("unconfirmed");
  });

  it("keeps a past-due rest workout scheduled rather than unconfirmed", () => {
    expect(
      derivedWorkoutStatus(
        workout("scheduled", "2026-07-01", "rest"),
        "2026-07-03",
      ),
    ).toBe("scheduled");
  });

  it("keeps scheduled for today and future dates", () => {
    expect(
      derivedWorkoutStatus(workout("scheduled", "2026-07-03"), "2026-07-03"),
    ).toBe("scheduled");
    expect(
      derivedWorkoutStatus(workout("scheduled", "2026-07-04"), "2026-07-03"),
    ).toBe("scheduled");
  });

  it("passes through resolved statuses untouched", () => {
    for (const status of ["completed", "skipped", "modified"]) {
      expect(
        derivedWorkoutStatus(workout(status, "2026-07-01"), "2026-07-03"),
      ).toBe(status);
    }
  });
});
