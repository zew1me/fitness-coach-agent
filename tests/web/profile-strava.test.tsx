// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
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

const EMPTY_METRICS = { best_times: [] };

/** The Strava panel is the <section> whose heading is "Strava". */
function stravaSection(): HTMLElement {
  const heading = screen.getByRole("heading", { name: "Strava" });
  const section = heading.closest("section");
  if (section === null) throw new Error("Strava section not found");
  return section as HTMLElement;
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("React", React);
  window.history.replaceState({}, "", "/profile");
  coachApiMocks.fetchBrowserToken.mockResolvedValue({
    access_token: "token-1",
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

describe("ProfilePage Strava connection", () => {
  it("shows the disclosure and connect action when disconnected", async () => {
    render(<ProfilePage />);

    const section = await waitFor(stravaSection);
    expect(within(section).getByText(/Not connected/i)).toBeTruthy();
    expect(within(section).getByText(/activity summaries only/i)).toBeTruthy();
    expect(
      within(section).getByRole("button", { name: "Connect with Strava" }),
    ).toBeTruthy();
  });

  it("starts authorization from the connect button", async () => {
    coachApiMocks.startStravaAuthorization.mockRejectedValueOnce(
      new Error("Strava integration is not enabled."),
    );

    render(<ProfilePage />);
    const section = await waitFor(stravaSection);

    fireEvent.click(
      within(section).getByRole("button", { name: "Connect with Strava" }),
    );

    await waitFor(() => {
      expect(coachApiMocks.startStravaAuthorization).toHaveBeenCalledTimes(1);
    });
    expect(
      await screen.findByText("Strava integration is not enabled."),
    ).toBeTruthy();
  });

  it("shows the connected athlete, scopes, and last sync", async () => {
    coachApiMocks.loadStravaStatus.mockResolvedValueOnce({
      connected: true,
      strava_athlete_id: 135168,
      strava_athlete_name: "Nigel S",
      scopes: ["read", "activity:read"],
      last_sync_at: "2026-07-21T07:00:00Z",
    });

    render(<ProfilePage />);
    const section = await waitFor(stravaSection);

    expect(within(section).getByText(/Connected as Nigel S/i)).toBeTruthy();
    expect(within(section).getByText(/Athlete 135168/)).toBeTruthy();
    expect(within(section).getByText(/read, activity:read/)).toBeTruthy();
    expect(within(section).getByText(/Last sync/i)).toBeTruthy();
  });

  it("syncs and shows imported counts including invalid records", async () => {
    coachApiMocks.loadStravaStatus.mockResolvedValueOnce({
      connected: true,
      strava_athlete_id: 135168,
      scopes: ["activity:read"],
    });
    coachApiMocks.syncStrava.mockResolvedValueOnce({
      activities: [{ id: "a1" }, { id: "a2" }],
      skipped_duplicates: 3,
      skipped_invalid: 1,
      synced: 2,
    });

    render(<ProfilePage />);
    const section = await waitFor(stravaSection);

    fireEvent.click(within(section).getByRole("button", { name: "Sync now" }));

    await waitFor(() => {
      expect(coachApiMocks.syncStrava).toHaveBeenCalledTimes(1);
    });
    expect(
      within(section).getByText(
        "Synced 2 (3 already imported; 1 couldn't be imported).",
      ),
    ).toBeTruthy();
  });

  it("re-enables the sync button after a failure", async () => {
    coachApiMocks.loadStravaStatus.mockResolvedValueOnce({
      connected: true,
      strava_athlete_id: 135168,
      scopes: ["activity:read"],
    });
    coachApiMocks.syncStrava.mockRejectedValueOnce(
      new Error("Strava rate limit reached. Try again after the next reset."),
    );

    render(<ProfilePage />);
    const section = await waitFor(stravaSection);
    const syncButton = within(section).getByRole("button", {
      name: "Sync now",
    });
    fireEvent.click(syncButton);

    expect(
      await within(section).findByText(/rate limit reached/i),
    ).toBeTruthy();
    await waitFor(() => {
      expect((syncButton as HTMLButtonElement).disabled).toBe(false);
    });
  });

  it("confirms deletion counts on disconnect", async () => {
    coachApiMocks.loadStravaStatus.mockResolvedValueOnce({
      connected: true,
      strava_athlete_id: 135168,
      scopes: ["activity:read"],
    });
    coachApiMocks.disconnectStrava.mockResolvedValueOnce({
      connected: false,
      scopes: [],
      deleted_activities: 4,
    });

    render(<ProfilePage />);
    const section = await waitFor(stravaSection);

    fireEvent.click(
      within(section).getByRole("button", { name: "Disconnect" }),
    );

    await waitFor(() => {
      expect(coachApiMocks.disconnectStrava).toHaveBeenCalledTimes(1);
    });
    expect(
      await within(section).findByText(
        /Disconnected from Strava. Deleted 4 imported activities./i,
      ),
    ).toBeTruthy();
  });

  it("surfaces a pending state when remote revocation is deferred", async () => {
    coachApiMocks.loadStravaStatus.mockResolvedValueOnce({
      connected: true,
      strava_athlete_id: 135168,
      scopes: ["activity:read"],
    });
    coachApiMocks.disconnectStrava.mockResolvedValueOnce({
      connected: true,
      disconnect_pending: true,
      strava_athlete_id: 135168,
      scopes: ["activity:read"],
      deleted_activities: 0,
    });

    render(<ProfilePage />);
    const section = await waitFor(stravaSection);

    fireEvent.click(
      within(section).getByRole("button", { name: "Disconnect" }),
    );

    // The connection stays visible with a pending notice (both the action error
    // and the panel's own pending paragraph reference the deferred revocation).
    expect(
      (await within(section).findAllByText(/could not be revoked yet/i)).length,
    ).toBeGreaterThan(0);
  });

  it("shows the scope-error notice from the OAuth callback", async () => {
    window.history.replaceState({}, "", "/profile?strava=scope_error");

    render(<ProfilePage />);

    expect(
      await screen.findByText(/did not grant activity access/i),
    ).toBeTruthy();
    expect(window.location.search).toBe("");
  });

  it("keeps Strava and Intervals actions independent", async () => {
    coachApiMocks.loadIntervalsStatus.mockResolvedValueOnce({
      connected: false,
      scopes: [],
    });
    coachApiMocks.loadStravaStatus.mockResolvedValueOnce({
      connected: true,
      strava_athlete_id: 135168,
      scopes: ["activity:read"],
    });
    coachApiMocks.syncStrava.mockRejectedValueOnce(new Error("Strava boom"));

    render(<ProfilePage />);
    const section = await waitFor(stravaSection);
    fireEvent.click(within(section).getByRole("button", { name: "Sync now" }));

    expect(await within(section).findByText("Strava boom")).toBeTruthy();
    // The Intervals connect button remains usable.
    expect(
      screen.getByRole("button", { name: /Connect Intervals.icu/i }),
    ).toBeTruthy();
  });
});
