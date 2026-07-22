// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const coachApiMocks = vi.hoisted(() => ({
  confirmProfileMetric: vi.fn(),
  confirmSportThreshold: vi.fn(),
  disconnectIntervals: vi.fn(),
  disconnectStrava: vi.fn(),
  fetchBrowserToken: vi.fn(),
  loadFitnessMetrics: vi.fn(),
  loadIntervalsStatus: vi.fn(),
  loadStravaStatus: vi.fn(),
  startIntervalsAuthorization: vi.fn(),
  startStravaAuthorization: vi.fn(),
  syncIntervals: vi.fn(),
  syncStrava: vi.fn(),
}));

vi.mock("../../lib/coach-api", () => coachApiMocks);

import ProfilePage from "../../app/profile/page";

const EMPTY_METRICS = {
  best_times: [],
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
  coachApiMocks.loadStravaStatus.mockResolvedValue({
    connected: false,
    scopes: [],
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("ProfilePage Intervals.icu connection", () => {
  it("shows a status card while the profile is loading", () => {
    coachApiMocks.fetchBrowserToken.mockReturnValueOnce(
      new Promise(() => undefined),
    );

    render(<ProfilePage />);

    expect(
      screen.getByRole("status", { name: "Loading profile…" }),
    ).toBeTruthy();
    expect(
      screen.getByText(/latest fitness metrics and connected services/i),
    ).toBeTruthy();
  });

  it("shows a status card when profile loading fails", async () => {
    coachApiMocks.loadFitnessMetrics.mockRejectedValueOnce(
      new Error("Fitness service unavailable."),
    );

    render(<ProfilePage />);

    expect(
      await screen.findByRole("alert", { name: "Unable to load profile" }),
    ).toBeTruthy();
    expect(screen.getByText("Fitness service unavailable.")).toBeTruthy();
  });

  it("shows the disconnected state and starts authorization from the connect button", async () => {
    coachApiMocks.startIntervalsAuthorization.mockRejectedValueOnce(
      new Error("Intervals.icu integration is not configured yet."),
    );

    render(<ProfilePage />);

    await screen.findByRole("heading", { name: "Fitness profile" });
    expect(await screen.findByText("Intervals.icu")).toBeTruthy();
    // Both provider panels render "Not connected"; Intervals + Strava.
    expect(screen.getAllByText(/Not connected/i)).toHaveLength(2);

    fireEvent.click(
      screen.getByRole("button", { name: /Connect Intervals.icu/i }),
    );

    await waitFor(() => {
      expect(coachApiMocks.startIntervalsAuthorization).toHaveBeenCalledTimes(
        1,
      );
    });
    expect(
      await screen.findByText(
        "Intervals.icu integration is not configured yet.",
      ),
    ).toBeTruthy();
  });

  it("shows the connected athlete and disconnects the account", async () => {
    coachApiMocks.loadIntervalsStatus.mockResolvedValueOnce({
      connected: true,
      intervals_athlete_id: "i135168",
      intervals_athlete_name: "Nigel",
      scopes: ["ACTIVITY:READ"],
      connected_at: "2026-07-08T07:00:00Z",
    });
    coachApiMocks.disconnectIntervals.mockResolvedValueOnce({
      connected: false,
      scopes: [],
    });

    render(<ProfilePage />);

    expect(await screen.findByText(/Connected as Nigel/i)).toBeTruthy();
    expect(screen.getByText(/i135168/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Disconnect/i }));

    await waitFor(() => {
      expect(coachApiMocks.disconnectIntervals).toHaveBeenCalledTimes(1);
    });
    // Intervals now disconnected too, so both panels show "Not connected".
    await waitFor(() => {
      expect(screen.getAllByText(/Not connected/i)).toHaveLength(2);
    });
  });

  it("syncs activities and shows the imported counts", async () => {
    coachApiMocks.loadIntervalsStatus.mockResolvedValueOnce({
      connected: true,
      intervals_athlete_id: "i135168",
      scopes: ["ACTIVITY:READ"],
    });
    coachApiMocks.syncIntervals.mockResolvedValueOnce({
      activities: [{ id: "activity-1" }, { id: "activity-2" }],
      skipped_duplicates: 3,
      skipped_invalid: 0,
      synced: 2,
    });

    render(<ProfilePage />);

    fireEvent.click(await screen.findByRole("button", { name: "Sync now" }));

    await waitFor(() => {
      expect(coachApiMocks.syncIntervals).toHaveBeenCalledTimes(1);
    });
    expect(
      await screen.findByText("Synced 2 (3 already imported)."),
    ).toBeTruthy();
  });

  it("re-enables sync after an error", async () => {
    coachApiMocks.loadIntervalsStatus.mockResolvedValueOnce({
      connected: true,
      intervals_athlete_id: "i135168",
      scopes: ["ACTIVITY:READ"],
    });
    coachApiMocks.syncIntervals.mockRejectedValueOnce(
      new Error("Intervals.icu is unavailable."),
    );

    render(<ProfilePage />);

    const syncButton = await screen.findByRole("button", { name: "Sync now" });
    fireEvent.click(syncButton);

    expect(
      await screen.findByText("Intervals.icu is unavailable."),
    ).toBeTruthy();
    await waitFor(() => {
      expect((syncButton as HTMLButtonElement).disabled).toBe(false);
    });
  });

  it("shows the callback success notice when Intervals redirects back", async () => {
    window.history.replaceState(
      {},
      "",
      "/profile?intervals=connected&tab=connections#linked",
    );

    render(<ProfilePage />);

    expect(await screen.findByText("Intervals.icu connected.")).toBeTruthy();
    expect(window.location.pathname).toBe("/profile");
    expect(window.location.search).toBe("?tab=connections");
    expect(window.location.hash).toBe("#linked");
  });
});
