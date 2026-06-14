// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchBrowserToken } from "../../lib/coach-api";
import { useBrowserSession } from "../../lib/use-browser-session";

vi.mock("../../lib/coach-api", () => ({
  fetchBrowserToken: vi.fn(),
}));

const fetchBrowserTokenMock = vi.mocked(fetchBrowserToken);

afterEach(() => {
  fetchBrowserTokenMock.mockReset();
});

describe("useBrowserSession", () => {
  it("starts in loading state with no token", () => {
    fetchBrowserTokenMock.mockReturnValue(new Promise(() => undefined));
    const { result } = renderHook(() => useBrowserSession());
    expect(result.current).toEqual({
      token: null,
      error: null,
      loading: true,
    });
  });

  it("resolves with the fetched token on success", async () => {
    const token = {
      access_token: "token-abc",
      token_type: "Bearer" as const,
      expires_at: "2099-12-31T00:00:00Z",
      scopes: ["chat"],
      user_id: "user-1",
    };
    fetchBrowserTokenMock.mockResolvedValueOnce(token);

    const { result } = renderHook(() => useBrowserSession());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.token).toEqual(token);
    expect(result.current.error).toBeNull();
  });

  it("surfaces the error message on failure", async () => {
    fetchBrowserTokenMock.mockRejectedValueOnce(new Error("offline"));

    const { result } = renderHook(() => useBrowserSession());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.token).toBeNull();
    expect(result.current.error).toBe("offline");
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
      resolve({
        access_token: "late",
        token_type: "Bearer" as const,
        expires_at: "2099-12-31T00:00:00Z",
        scopes: ["chat"],
        user_id: "user-late",
      });
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
