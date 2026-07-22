// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const coachApiMocks = vi.hoisted(() => ({
  loadCalendar: vi.fn(),
  resolvePlannedWorkout: vi.fn(),
}));
const sessionMocks = vi.hoisted(() => ({ useBrowserSession: vi.fn() }));

vi.mock("../../lib/coach-api", () => coachApiMocks);
vi.mock("../../lib/use-browser-session", () => sessionMocks);

import { CoachCalendar } from "../../components/coach-calendar";

// The calendar window is anchored on the viewer's local "today", so anchor the
// fixture workout there too — it always lands in a rendered week.
const todayIso = new Intl.DateTimeFormat("en-CA").format(new Date());

function plannedWorkout(
  overrides: Record<string, unknown>,
): Record<string, unknown> {
  return {
    id: "workout-1",
    plan_id: "plan-1",
    workout_date: todayIso,
    sport: "cycling",
    title: "Endurance ride",
    workout_type: "endurance",
    status: "scheduled",
    ...overrides,
  };
}

function calendarResponse(
  planned: Array<Record<string, unknown>>,
): Record<string, unknown> {
  return {
    start: todayIso,
    end: todayIso,
    planned_workouts: planned,
    activities: [],
  };
}

function renderCalendar(): ReturnType<typeof render> {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <CoachCalendar />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("React", React);
  sessionMocks.useBrowserSession.mockReturnValue({
    token: {
      access_token: "token-1",
      expires_at: "2026-08-01T00:00:00Z",
      scopes: ["profile:read"],
      token_type: "Bearer",
      user_id: "athlete-1",
    },
    error: null,
    loading: false,
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("CoachCalendar nutrition focus", () => {
  it("shows a per-week fueling line when the active plan carries a focus", async () => {
    coachApiMocks.loadCalendar.mockResolvedValue(
      calendarResponse([
        plannedWorkout({
          nutrition_focus: "Race week: practise event-day fuelling.",
        }),
      ]),
    );

    renderCalendar();

    expect(
      await screen.findByText("Race week: practise event-day fuelling."),
    ).toBeTruthy();
    expect(screen.getByText("Fuel")).toBeTruthy();
  });

  it("omits the fueling line when no workout in the week has a focus", async () => {
    coachApiMocks.loadCalendar.mockResolvedValue(
      calendarResponse([plannedWorkout({ nutrition_focus: "" })]),
    );

    renderCalendar();

    // The workout chip renders once the query resolves, but no "Fuel" banner.
    await screen.findByTestId("calendar-chip-planned");
    expect(screen.queryByText("Fuel")).toBeNull();
  });
});
