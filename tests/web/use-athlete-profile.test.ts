// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { loadProfile, saveProfile } from "../../lib/coach-api";
import { useAthleteProfile } from "../../lib/use-athlete-profile";

vi.mock("../../lib/coach-api", () => ({
  loadProfile: vi.fn(),
  saveProfile: vi.fn(),
}));

const loadProfileMock = vi.mocked(loadProfile);
const saveProfileMock = vi.mocked(saveProfile);

const TOKEN = {
  access_token: "tok",
  token_type: "Bearer" as const,
  expires_at: "2099-12-31T00:00:00Z",
  scopes: ["chat"],
  user_id: "u-1",
};

const PROFILE = {
  user_id: "u-1",
  coaching_state: "active" as const,
  primary_sports: ["cycling"],
  display_name: "Sam",
};

afterEach(() => {
  loadProfileMock.mockReset();
  saveProfileMock.mockReset();
});

describe("useAthleteProfile", () => {
  it("does nothing with a null token", () => {
    const { result } = renderHook(() => useAthleteProfile(null));
    expect(result.current.profile).toBeNull();
    expect(loadProfileMock).not.toHaveBeenCalled();
  });

  it("ensureLoaded is a no-op when called with a null token", async () => {
    const { result } = renderHook(() => useAthleteProfile(null));
    await act(async () => {
      await result.current.ensureLoaded();
    });
    expect(loadProfileMock).not.toHaveBeenCalled();
    expect(result.current.saving).toBe(false);
  });

  it("prefetch failure surfaces a drawer status and logs the error", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    loadProfileMock.mockRejectedValueOnce(new Error("nope"));

    const { result } = renderHook(() => useAthleteProfile(TOKEN));

    await waitFor(() => {
      expect(result.current.status).not.toBeNull();
    });
    expect(result.current.status).toMatch(/Couldn't load your saved profile/i);
    expect(errorSpy).toHaveBeenCalled();
    errorSpy.mockRestore();
  });

  it("prefetches the profile when given a token", async () => {
    loadProfileMock.mockResolvedValueOnce(PROFILE);
    const { result } = renderHook(() => useAthleteProfile(TOKEN));

    await waitFor(() => {
      expect(result.current.profile).toEqual(PROFILE);
    });
  });

  it("falls back to an empty profile when prefetch fails", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    loadProfileMock.mockRejectedValueOnce(new Error("nope"));
    const { result } = renderHook(() => useAthleteProfile(TOKEN));

    await waitFor(() => {
      expect(result.current.profile).not.toBeNull();
    });
    expect(result.current.profile).toMatchObject({
      user_id: "u-1",
      coaching_state: "onboarding",
      primary_sports: [],
    });
    errorSpy.mockRestore();
  });

  it("ensureLoaded is a no-op once profile is loaded", async () => {
    loadProfileMock.mockResolvedValueOnce(PROFILE);
    const { result } = renderHook(() => useAthleteProfile(TOKEN));
    await waitFor(() => {
      expect(result.current.profile).toEqual(PROFILE);
    });

    loadProfileMock.mockClear();
    await act(async () => {
      await result.current.ensureLoaded();
    });
    expect(loadProfileMock).not.toHaveBeenCalled();
  });

  it("save returns the persisted profile and stamps a success status", async () => {
    loadProfileMock.mockResolvedValueOnce(PROFILE);
    saveProfileMock.mockImplementation((input) =>
      Promise.resolve({
        ...input,
        display_name: input.display_name ?? "Sam",
      }),
    );
    const { result } = renderHook(() => useAthleteProfile(TOKEN));
    await waitFor(() => {
      expect(result.current.profile).toEqual(PROFILE);
    });

    let saved: Awaited<ReturnType<typeof result.current.save>> = null;
    await act(async () => {
      saved = await result.current.save();
    });
    expect(saved).toMatchObject({ display_name: "Sam" });
    expect(result.current.status).toBe("Saved your athlete settings.");
    expect(result.current.saving).toBe(false);
  });

  it("save surfaces the error message and returns null on failure", async () => {
    loadProfileMock.mockResolvedValueOnce(PROFILE);
    saveProfileMock.mockRejectedValueOnce(new Error("bad request"));

    const { result } = renderHook(() => useAthleteProfile(TOKEN));
    await waitFor(() => {
      expect(result.current.profile).toEqual(PROFILE);
    });

    let saved: Awaited<ReturnType<typeof result.current.save>> = null;
    await act(async () => {
      saved = await result.current.save();
    });
    expect(saved).toBeNull();
    expect(result.current.status).toBe("bad request");
  });
});
