/**
 * Hermetic e2e coverage for the /calendar view (issue #212 MVP).
 *
 * The clock is pinned to Friday 2026-07-03 so the 42-day-back / 8-week-ahead
 * window is deterministic: Monday 2026-05-18 → Sunday 2026-08-30 (105 days).
 */
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import { mockAuthenticatedSession, TEST_USER_ID } from "./helpers/session";

const FIXED_TODAY = "2026-07-03";
const EXPECTED_START = "2026-05-18";
const EXPECTED_END = "2026-08-30";
const EXPECTED_DAY_COUNT = 105;

const CALENDAR_FIXTURE = {
  planned_workouts: [
    {
      id: "workout-upcoming",
      plan_id: "plan-1",
      user_id: TEST_USER_ID,
      workout_date: "2026-07-04",
      day_of_week: 5,
      week_number: 2,
      phase_name: "Build",
      sport: "cycling",
      title: "Sweet spot 3x12",
      description: "3x12min at 90% FTP with 5min recoveries.",
      workout_type: "sweet_spot",
      target_duration_minutes: 75,
      target_tss: 80,
      status: "scheduled",
      actual_activity_id: null,
    },
    {
      id: "workout-unconfirmed",
      plan_id: "plan-1",
      user_id: TEST_USER_ID,
      workout_date: "2026-07-01",
      day_of_week: 2,
      week_number: 2,
      phase_name: "Build",
      sport: "cycling",
      title: "Endurance spin",
      workout_type: "endurance",
      target_duration_minutes: 60,
      status: "scheduled",
      actual_activity_id: null,
    },
    {
      id: "workout-done",
      plan_id: "plan-1",
      user_id: TEST_USER_ID,
      workout_date: "2026-06-20",
      day_of_week: 5,
      week_number: 0,
      sport: "running",
      title: "Easy shakeout",
      workout_type: "recovery",
      target_duration_minutes: 40,
      status: "completed",
      actual_activity_id: "activity-past",
    },
  ],
  activities: [
    {
      id: "activity-past",
      user_id: TEST_USER_ID,
      sport: "running",
      activity_date: "2026-06-20",
      started_at: "2026-06-20T06:30:00Z",
      duration_seconds: 3600,
      distance_meters: 12000,
      tss: 68,
      rpe: 6,
      athlete_notes: "Felt strong on the hills.",
      planned_workout_id: "workout-done",
    },
  ],
};

async function mockCalendarView(page: Page): Promise<void> {
  await page.clock.setFixedTime(new Date(`${FIXED_TODAY}T10:00:00`));
  await mockAuthenticatedSession(page);
  await page.route("**/api/calendar**", (route) => {
    const url = new URL(route.request().url());
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        start: url.searchParams.get("start"),
        end: url.searchParams.get("end"),
        ...CALENDAR_FIXTURE,
      }),
    });
  });
}

test("renders the full 15-week window with today highlighted", async ({
  page,
}) => {
  await mockCalendarView(page);
  await page.goto("/calendar");

  const grid = page.getByTestId("calendar-grid");
  await expect(grid).toBeVisible();

  const days = page.getByTestId("calendar-day");
  await expect(days).toHaveCount(EXPECTED_DAY_COUNT);
  await expect(days.first()).toHaveAttribute("data-date", EXPECTED_START);
  await expect(days.last()).toHaveAttribute("data-date", EXPECTED_END);

  const today = page.locator('[data-testid="calendar-day"][data-today="true"]');
  await expect(today).toHaveCount(1);
  await expect(today).toHaveAttribute("data-date", FIXED_TODAY);
  await expect(today).toContainText("Today");

  // Month headings appear as the grid crosses month boundaries.
  await expect(page.getByRole("heading", { name: "May 2026" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "August 2026" }),
  ).toBeVisible();
});

test("shows planned workouts and recorded activities as day chips", async ({
  page,
}) => {
  await mockCalendarView(page);
  await page.goto("/calendar");

  const upcomingDay = page.locator(
    '[data-testid="calendar-day"][data-date="2026-07-04"]',
  );
  const upcomingPlan = upcomingDay.getByTestId("calendar-chip-planned");
  await expect(upcomingPlan).toContainText("○cycling: Sweet spot 3x12");
  await expect(upcomingPlan).toHaveAttribute("data-entry-kind", "planned");
  await expect(upcomingPlan).toHaveAttribute("data-status", "scheduled");
  await expect(upcomingPlan).toHaveAttribute("data-workout-family", "tempo");

  const pastDay = page.locator(
    '[data-testid="calendar-day"][data-date="2026-06-20"]',
  );
  const completedPlan = pastDay.getByTestId("calendar-chip-planned");
  await expect(pastDay).toHaveAccessibleName(
    /1 planned workout\. 1 recorded activity\./,
  );
  await expect(completedPlan).toHaveAttribute("data-status", "completed");
  await expect(completedPlan).toHaveAttribute(
    "data-workout-family",
    "recovery",
  );
  await expect(completedPlan).toContainText("✓running: Easy shakeout");

  const recordedActivity = pastDay.getByTestId("calendar-chip-activity");
  await expect(recordedActivity).toHaveAttribute("data-entry-kind", "recorded");
  await expect(recordedActivity).toContainText("●Recorded running · 1h");

  await expect(page.getByLabel("Workout type colors")).toContainText(
    "VO₂ / anaerobic / race",
  );
  await expect(page.getByLabel("Workout entry markers")).toContainText(
    "Scheduled plan",
  );
  await expect(page.getByLabel("Workout entry markers")).toContainText(
    "Completed plan",
  );
  await expect(page.getByLabel("Workout entry markers")).toContainText(
    "Skipped plan",
  );
  await expect(page.getByLabel("Workout entry markers")).toContainText(
    "Modified plan",
  );
  await expect(page.getByLabel("Workout entry markers")).toContainText(
    "Unconfirmed plan",
  );
  await expect(page.getByLabel("Workout entry markers")).toContainText(
    "Recorded activity",
  );
});

test("opens a read-only day detail panel with full session context", async ({
  page,
}) => {
  await mockCalendarView(page);
  await page.goto("/calendar");

  await page
    .locator('[data-testid="calendar-day"][data-date="2026-06-20"]')
    .click();

  const detail = page.getByTestId("calendar-day-detail");
  await expect(detail).toBeVisible();
  await expect(detail).toHaveAttribute("role", "dialog");
  await expect(page.getByTestId("calendar-detail-close")).toBeFocused();
  await expect(detail).toContainText("Saturday, June 20, 2026");
  await expect(detail).toContainText("Easy shakeout");
  await expect(detail).toContainText("completed");
  await expect(detail).toContainText("12.0 km");
  await expect(detail).toContainText("RPE 6");
  await expect(detail).toContainText("Felt strong on the hills.");

  await page.getByTestId("calendar-detail-close").click();
  await expect(detail).not.toBeVisible();

  // An empty day still opens the panel, with an explicit empty state.
  await page
    .locator('[data-testid="calendar-day"][data-date="2026-08-30"]')
    .click();
  await expect(page.getByTestId("calendar-day-detail")).toContainText(
    "Nothing planned or recorded for this day.",
  );

  // Escape also closes the dialog, returning focus to the day cell.
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("calendar-day-detail")).not.toBeVisible();
  await expect(
    page.locator('[data-testid="calendar-day"][data-date="2026-08-30"]'),
  ).toBeFocused();
});

test("confirms an unconfirmed past workout from the day detail panel", async ({
  page,
}) => {
  // After a successful resolve, the refetched calendar returns the workout
  // as completed so the UI can be asserted to reflect the new state.
  let resolved = false;
  await page.clock.setFixedTime(new Date(`${FIXED_TODAY}T10:00:00`));
  await mockAuthenticatedSession(page);
  await page.route("**/api/calendar**", (route) => {
    const url = new URL(route.request().url());
    const fixture = resolved
      ? {
          ...CALENDAR_FIXTURE,
          planned_workouts: CALENDAR_FIXTURE.planned_workouts.map((workout) =>
            workout.id === "workout-unconfirmed"
              ? { ...workout, status: "completed" }
              : workout,
          ),
        }
      : CALENDAR_FIXTURE;
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        start: url.searchParams.get("start"),
        end: url.searchParams.get("end"),
        ...fixture,
      }),
    });
  });
  const resolveRequests: Array<Record<string, unknown>> = [];
  await page.route("**/api/engine/resolve-plan-workout", (route) => {
    resolveRequests.push(
      route.request().postDataJSON() as Record<string, unknown>,
    );
    resolved = true;
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        workout: {
          id: "workout-unconfirmed",
          plan_id: "plan-1",
          workout_date: "2026-07-01",
          sport: "cycling",
          title: "Endurance spin",
          workout_type: "endurance",
          status: "completed",
        },
      }),
    });
  });
  await page.goto("/calendar");

  // A past, still-scheduled workout reads as unconfirmed and offers actions.
  await page
    .locator('[data-testid="calendar-day"][data-date="2026-07-01"]')
    .click();
  const detail = page.getByTestId("calendar-day-detail");
  await expect(detail).toContainText("Endurance spin");
  await expect(detail).toContainText("unconfirmed");

  await page.getByTestId("calendar-resolve-completed").click();
  await expect.poll(() => resolveRequests.length, { timeout: 5000 }).toBe(1);
  expect(resolveRequests[0]).toEqual({
    outcome: "completed",
    plan_workout_id: "workout-unconfirmed",
    source: "athlete",
  });

  // The refetched calendar shows the workout as completed with no actions.
  await expect(detail).toContainText("completed");
  await expect(
    page.getByTestId("calendar-resolve-completed"),
  ).not.toBeVisible();

  // The upcoming workout stays a plain scheduled session with no actions.
  await page.getByTestId("calendar-detail-close").click();
  await page
    .locator('[data-testid="calendar-day"][data-date="2026-07-04"]')
    .click();
  await expect(page.getByTestId("calendar-day-detail")).toContainText(
    "scheduled",
  );
  await expect(
    page.getByTestId("calendar-resolve-completed"),
  ).not.toBeVisible();
});

test("navigates chat → calendar → chat via the topbar toggles", async ({
  page,
}) => {
  await mockCalendarView(page);
  await page.goto("/");

  await page.getByTestId("chat-open-calendar").click();
  await expect(page).toHaveURL(/\/calendar$/);
  await expect(page.getByTestId("calendar-grid")).toBeVisible();

  await page.getByTestId("calendar-open-chat").click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId("chat-open-calendar")).toBeVisible();
});

test("shows the signed-out landing when there is no browser session", async ({
  page,
}) => {
  await page.route("**/api/oauth/browser-token", (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ detail: "no session" }),
    }),
  );
  await page.goto("/calendar");

  await expect(
    page.getByRole("link", { name: "Continue with magic link" }),
  ).toBeVisible();
  await expect(page.getByTestId("calendar-grid")).not.toBeVisible();
});
