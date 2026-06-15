// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useIsMobile } from "../../lib/use-is-mobile";

type MediaQueryListener = (event: MediaQueryListEvent) => void;

function createMediaQueryListMock(initialMatches: boolean): {
  mql: MediaQueryList;
  emit: (matches: boolean) => void;
} {
  let matches = initialMatches;
  const listeners = new Set<MediaQueryListener>();
  const mql = {
    get matches(): boolean {
      return matches;
    },
    addEventListener(_type: string, handler: MediaQueryListener): void {
      listeners.add(handler);
    },
    removeEventListener(_type: string, handler: MediaQueryListener): void {
      listeners.delete(handler);
    },
  } as unknown as MediaQueryList;
  return {
    mql,
    emit(next: boolean): void {
      matches = next;
      const event = { matches: next } as MediaQueryListEvent;
      listeners.forEach((listener) => listener(event));
    },
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useIsMobile", () => {
  it("returns false on a desktop-width viewport", () => {
    const { mql } = createMediaQueryListMock(false);
    vi.spyOn(window, "matchMedia").mockReturnValue(mql);

    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
  });

  it("returns true on a mobile-width viewport", () => {
    const { mql } = createMediaQueryListMock(true);
    vi.spyOn(window, "matchMedia").mockReturnValue(mql);

    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);
  });

  it("responds to viewport changes after mount", () => {
    const { mql, emit } = createMediaQueryListMock(false);
    vi.spyOn(window, "matchMedia").mockReturnValue(mql);

    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);

    act(() => {
      emit(true);
    });
    expect(result.current).toBe(true);

    act(() => {
      emit(false);
    });
    expect(result.current).toBe(false);
  });
});
