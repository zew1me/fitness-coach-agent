// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, fetchBrowserToken } from "../../lib/coach-api";
import type { BrowserTokenResponse } from "../../lib/types";
import { useBrowserSession } from "../../lib/use-browser-session";

vi.mock("../../lib/coach-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/coach-api")>();
  return { ...actual, fetchBrowserToken: vi.fn() };
});

const fetchBrowserTokenMock = vi.mocked(fetchBrowserToken);

function browserToken(
  accessToken = "token-abc",
  expiresAt = "2099-12-31T00:00:00Z",
): BrowserTokenResponse {
  return {
    access_token: accessToken,
    token_type: "Bearer",
    expires_at: expiresAt,
    scopes: ["chat"],
    user_id: "user-1",
  };
}

afterEach(() => {
  cleanup();
  fetchBrowserTokenMock.mockReset();
  vi.useRealTimers();
});

describe("useBrowserSession", () => {
  it("starts in loading state with no token", () => {
    fetchBrowserTokenMock.mockReturnValue(new Promise(() => undefined));
    const { result } = renderHook(() => useBrowserSession());
    expect(result.current).toEqual({
      authenticationRequired: false,
      token: null,
      error: null,
      loading: true,
      refresh: expect.any(Function),
    });
  });

  it("resolves with the fetched token on success", async () => {
    const token = browserToken();
    fetchBrowserTokenMock.mockResolvedValueOnce(token);

    const { result } = renderHook(() => useBrowserSession());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.token).toEqual(token);
    expect(result.current.error).toBeNull();
    expect(result.current.authenticationRequired).toBe(false);
  });

  it("surfaces the error message on bootstrap failure", async () => {
    fetchBrowserTokenMock.mockRejectedValueOnce(new Error("offline"));

    const { result } = renderHook(() => useBrowserSession());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.token).toBeNull();
    expect(result.current.error).toBe("offline");
    expect(result.current.authenticationRequired).toBe(false);
  });

  it("turns a 401 into an explicit sign-in-required state", async () => {
    fetchBrowserTokenMock.mockRejectedValueOnce(
      new ApiError(401, "Invalid browser session cookie."),
    );

    const { result } = renderHook(() => useBrowserSession());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.token).toBeNull();
    expect(result.current.authenticationRequired).toBe(true);
    // The expiry copy lives solely in the reauthentication screen, so this
    // state carries no message — which also cannot leak the raw 401 detail.
    expect(result.current.error).toBeNull();
  });

  it("renews the browser session when the tab regains focus", async () => {
    fetchBrowserTokenMock
      .mockResolvedValueOnce(browserToken("token-1"))
      .mockResolvedValueOnce(browserToken("token-2"));

    const { result } = renderHook(() => useBrowserSession());
    await waitFor(() => {
      expect(result.current.token?.access_token).toBe("token-1");
    });

    act(() => {
      window.dispatchEvent(new Event("focus"));
    });

    await waitFor(() => {
      expect(result.current.token?.access_token).toBe("token-2");
    });
    expect(fetchBrowserTokenMock).toHaveBeenCalledTimes(2);
  });

  it("throttles repeated focus and visibility renewals", async () => {
    vi.useFakeTimers();
    // The far-future expiry clamps the scheduled renewal to the 10-minute
    // maximum, so nothing but the lifecycle events moves the call count here.
    fetchBrowserTokenMock.mockResolvedValue(browserToken("token-1"));

    const { result } = renderHook(() => useBrowserSession());
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.token?.access_token).toBe("token-1");
    expect(fetchBrowserTokenMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      await Promise.resolve();
    });
    expect(fetchBrowserTokenMock).toHaveBeenCalledTimes(2);

    // A second focus and a visibility change inside the window are dropped.
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    expect(fetchBrowserTokenMock).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      await Promise.resolve();
    });
    expect(fetchBrowserTokenMock).toHaveBeenCalledTimes(3);
  });

  it("moves to sign-in-required when a focus renewal finds an expired cookie", async () => {
    fetchBrowserTokenMock
      .mockResolvedValueOnce(browserToken("token-1"))
      .mockRejectedValueOnce(
        new ApiError(401, "No browser session cookie is present."),
      );

    const { result } = renderHook(() => useBrowserSession());
    await waitFor(() => {
      expect(result.current.token?.access_token).toBe("token-1");
    });

    act(() => {
      window.dispatchEvent(new Event("focus"));
    });

    await waitFor(() => {
      expect(result.current.authenticationRequired).toBe(true);
    });
    expect(result.current.token).toBeNull();
  });

  it("keeps the current token through a transient background renewal failure", async () => {
    fetchBrowserTokenMock
      .mockResolvedValueOnce(browserToken("token-1"))
      .mockRejectedValueOnce(new TypeError("Load failed"));

    const { result } = renderHook(() => useBrowserSession());
    await waitFor(() => {
      expect(result.current.token?.access_token).toBe("token-1");
    });

    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => {
      expect(fetchBrowserTokenMock).toHaveBeenCalledTimes(2);
    });

    expect(result.current.token?.access_token).toBe("token-1");
    expect(result.current.authenticationRequired).toBe(false);
  });

  it("automatically retries a transient timer renewal failure", async () => {
    vi.useFakeTimers();
    const expiringSoon = new Date(Date.now() + 61_000).toISOString();
    fetchBrowserTokenMock
      .mockResolvedValueOnce(browserToken("token-1", expiringSoon))
      .mockRejectedValueOnce(new TypeError("Load failed"))
      .mockResolvedValueOnce(browserToken("token-2"));

    const { result } = renderHook(() => useBrowserSession());
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.token?.access_token).toBe("token-1");

    // The near-expiry token schedules its normal renewal at the 5-second
    // minimum. That attempt fails but keeps the existing authenticated UI.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(fetchBrowserTokenMock).toHaveBeenCalledTimes(2);
    expect(result.current.token?.access_token).toBe("token-1");

    // The first bounded backoff is 30 seconds; no focus or visibility event is
    // needed for the next attempt to recover the sliding session.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(29_999);
    });
    expect(fetchBrowserTokenMock).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(fetchBrowserTokenMock).toHaveBeenCalledTimes(3);
    expect(result.current.token?.access_token).toBe("token-2");
  });

  it("stops automatic renewal retries after the bounded backoff series", async () => {
    vi.useFakeTimers();
    const expiringSoon = new Date(Date.now() + 61_000).toISOString();
    fetchBrowserTokenMock
      .mockResolvedValueOnce(browserToken("token-1", expiringSoon))
      .mockRejectedValue(new TypeError("Load failed"));

    const { result } = renderHook(() => useBrowserSession());
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    for (const delayMs of [5_000, 30_000, 120_000, 300_000]) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(delayMs);
      });
    }
    expect(fetchBrowserTokenMock).toHaveBeenCalledTimes(5);
    expect(result.current.token?.access_token).toBe("token-1");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(24 * 60 * 60_000);
    });
    expect(fetchBrowserTokenMock).toHaveBeenCalledTimes(5);
  });

  it("falls back to a generic message when the failure is not an Error", async () => {
    fetchBrowserTokenMock.mockRejectedValueOnce("network gremlin");

    const { result } = renderHook(() => useBrowserSession());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.error).toBe(
      "Unable to connect your browser session.",
    );
  });

  it("ignores a resolved token after unmount", async () => {
    let resolve!: (_value: unknown) => void;
    const pending = new Promise((r) => {
      resolve = r;
    });
    fetchBrowserTokenMock.mockReturnValueOnce(
      pending as ReturnType<typeof fetchBrowserToken>,
    );
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    const { result, unmount } = renderHook(() => useBrowserSession());
    expect(result.current.loading).toBe(true);
    const lastSnapshot = result.current;

    unmount();
    await act(async () => {
      resolve(browserToken("late"));
      await pending.catch(() => undefined);
    });
    // After unmount, the hook must not call setState — React would warn via
    // console.error, and the last rendered snapshot must remain stable.
    const reactWarnings = errorSpy.mock.calls.filter(
      (args) =>
        typeof args[0] === "string" &&
        args[0].includes("Can't perform a React state update on an unmounted"),
    );
    expect(reactWarnings).toHaveLength(0);
    expect(result.current).toBe(lastSnapshot);
    errorSpy.mockRestore();
  });
});
