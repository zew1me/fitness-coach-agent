// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const coachApiMocks = vi.hoisted(() => ({
  confirmProfileMetric: vi.fn(),
  confirmSportThreshold: vi.fn(),
  disconnectIntervals: vi.fn(),
  fetchBrowserToken: vi.fn(),
  loadFitnessMetrics: vi.fn(),
  loadIntervalsStatus: vi.fn(),
  loadProfile: vi.fn(),
  startIntervalsAuthorization: vi.fn(),
  syncIntervals: vi.fn(),
}));

vi.mock("../../lib/coach-api", () => coachApiMocks);

import ProfilePage from "../../app/profile/page";

const EMPTY_METRICS = {
  best_times: [],
};

const BASE_PROFILE = {
  coaching_state: "active",
  primary_sports: ["cycling"],
  user_id: "coach-user-1",
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("React", React);
  window.history.replaceState({}, "", "/profile");
  coachApiMocks.fetchBrowserToken.mockResolvedValue({
    access_token: "token-1",
    expires_at: "2026-07-08T07:00:00Z",
    scopes: ["profile:read"],
    token_type: "Bearer",
    user_id: "coach-user-1",
  });
  coachApiMocks.loadFitnessMetrics.mockResolvedValue(EMPTY_METRICS);
  coachApiMocks.loadIntervalsStatus.mockResolvedValue({
    connected: false,
    scopes: [],
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("ProfilePage nutrition section", () => {
  it("renders dietary restrictions and notes when present", async () => {
    coachApiMocks.loadProfile.mockResolvedValue({
      ...BASE_PROFILE,
      dietary_restrictions: ["vegetarian", "gluten-free"],
      nutrition_notes: "Fuels with 60g carbs/hr on long rides.",
    });

    render(<ProfilePage />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Nutrition" })).toBeTruthy();
    });
    expect(screen.getByText("vegetarian, gluten-free")).toBeTruthy();
    expect(
      screen.getByText("Fuels with 60g carbs/hr on long rides."),
    ).toBeTruthy();
  });

  it("hides the section when no nutrition context is captured", async () => {
    coachApiMocks.loadProfile.mockResolvedValue({
      ...BASE_PROFILE,
      dietary_restrictions: [],
      nutrition_notes: null,
    });

    render(<ProfilePage />);

    // Wait for the loaded view (personal-bests logic runs once metrics resolve).
    await waitFor(() => {
      expect(coachApiMocks.loadProfile).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.queryByText("Loading profile…")).toBeNull();
    });
    expect(screen.queryByRole("heading", { name: "Nutrition" })).toBeNull();
  });

  it("hides the section when notes are whitespace-only and no restrictions", async () => {
    coachApiMocks.loadProfile.mockResolvedValue({
      ...BASE_PROFILE,
      dietary_restrictions: [],
      nutrition_notes: "   ",
    });

    render(<ProfilePage />);

    await waitFor(() => {
      expect(coachApiMocks.loadProfile).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.queryByText("Loading profile…")).toBeNull();
    });
    expect(screen.queryByRole("heading", { name: "Nutrition" })).toBeNull();
  });
});
