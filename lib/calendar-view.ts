import type {
  CalendarActivity,
  CalendarPlannedWorkout,
  CalendarResponse,
} from "./schemas";

/** Issue #212 MVP window: the past 42 days, today, and the upcoming 8 weeks. */
export const CALENDAR_PAST_DAYS = 42;
export const CALENDAR_FUTURE_DAYS = 56;

export type CalendarDayItems = {
  planned: CalendarPlannedWorkout[];
  activities: CalendarActivity[];
};

// All helpers operate on "YYYY-MM-DD" strings via UTC arithmetic so results
// are identical regardless of the viewer's timezone or DST transitions.
function toUtc(iso: string): Date {
  return new Date(`${iso}T00:00:00Z`);
}

function toIso(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function addDays(iso: string, days: number): string {
  const date = toUtc(iso);
  date.setUTCDate(date.getUTCDate() + days);
  return toIso(date);
}

/** Days since Monday (0 = Monday … 6 = Sunday). */
function mondayOffset(iso: string): number {
  return (toUtc(iso).getUTCDay() + 6) % 7;
}

/**
 * The full calendar window around `todayIso`, widened outward so the grid
 * starts on a Monday and ends on a Sunday.
 */
export function calendarDateRange(todayIso: string): {
  start: string;
  end: string;
} {
  const rawStart = addDays(todayIso, -CALENDAR_PAST_DAYS);
  const rawEnd = addDays(todayIso, CALENDAR_FUTURE_DAYS);
  const start = addDays(rawStart, -mondayOffset(rawStart));
  const end = addDays(rawEnd, 6 - mondayOffset(rawEnd));
  return { start, end };
}

/** Split a Monday-aligned range into weeks of seven ISO dates. */
export function buildCalendarWeeks(start: string, end: string): string[][] {
  const weeks: string[][] = [];
  for (let day = start; day <= end; day = addDays(day, 7)) {
    const week: string[] = [];
    for (let offset = 0; offset < 7; offset += 1) {
      week.push(addDays(day, offset));
    }
    weeks.push(week);
  }
  return weeks;
}

export function monthLabel(iso: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(toUtc(iso));
}

/** Mirrors backend `_NON_TRAINING_TYPES` (backend/services/compliance.py) — these
 * workout types are excluded from matching and never counted as unconfirmed. */
const NON_TRAINING_TYPES = new Set(["rest"]);

/**
 * "unconfirmed" is derived, never persisted: a past-dated workout still in
 * `scheduled` has neither auto-matched an activity nor been resolved by the
 * athlete or coach.
 */
export function derivedWorkoutStatus(
  workout: CalendarPlannedWorkout,
  todayIso: string,
): string {
  if (
    workout.status === "scheduled" &&
    workout.workout_date < todayIso &&
    !NON_TRAINING_TYPES.has(workout.workout_type)
  ) {
    return "unconfirmed";
  }
  return workout.status;
}

export function groupCalendarItemsByDay(
  response: CalendarResponse,
): Map<string, CalendarDayItems> {
  const byDay = new Map<string, CalendarDayItems>();
  const dayFor = (iso: string): CalendarDayItems => {
    const existing = byDay.get(iso);
    if (existing !== undefined) return existing;
    const created: CalendarDayItems = { planned: [], activities: [] };
    byDay.set(iso, created);
    return created;
  };
  for (const workout of response.planned_workouts) {
    dayFor(workout.workout_date).planned.push(workout);
  }
  for (const activity of response.activities) {
    dayFor(activity.activity_date).activities.push(activity);
  }
  return byDay;
}
