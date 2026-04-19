// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useTheme } from "../../lib/use-theme";

const STORAGE_KEY = "fitness-theme-preference";

type LocalStorageMock = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
  clear(): void;
};

const localStorageMock: LocalStorageMock = ((): LocalStorageMock => {
  let store: Record<string, string> = {};
  return {
    getItem(key: string): string | null { return store[key] ?? null; },
    setItem(key: string, value: string): void { store[key] = value; },
    removeItem(key: string): void { delete store[key]; },
    clear(): void { store = {}; },
  };
})();

beforeEach(() => {
  vi.stubGlobal("localStorage", localStorageMock);
  localStorageMock.clear();
  document.documentElement.dataset["theme"] = "light";
  document.documentElement.dataset["themeMode"] = "system";
  vi.spyOn(window, "matchMedia").mockReturnValue({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  } as unknown as MediaQueryList);
  // intentional — mock setup complete
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorageMock.clear();
});

describe("useTheme", () => {
  it("reads system preference as the initial mode when nothing is stored", () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.mode).toBe("system");
  });

  it("reads saved mode from localStorage on mount", () => {
    localStorage.setItem(STORAGE_KEY, "dark");
    const { result } = renderHook(() => useTheme());
    expect(result.current.mode).toBe("dark");
  });

  it("setTheme('dark') sets data-theme=dark on <html>", () => {
    const { result } = renderHook(() => useTheme());
    act(() => {
      result.current.setTheme("dark");
    });
    expect(document.documentElement.dataset["theme"]).toBe("dark");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("dark");
  });

  it("setTheme('light') sets data-theme=light on <html>", () => {
    const { result } = renderHook(() => useTheme());
    act(() => {
      result.current.setTheme("light");
    });
    expect(document.documentElement.dataset["theme"]).toBe("light");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("light");
  });

  it("setTheme('system') resolves to light when OS is light", () => {
    const { result } = renderHook(() => useTheme());
    act(() => {
      result.current.setTheme("system");
    });
    expect(document.documentElement.dataset["theme"]).toBe("light");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("system");
  });

  it("setTheme('system') resolves to dark when OS is dark", () => {
    vi.spyOn(window, "matchMedia").mockReturnValue({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    } as unknown as MediaQueryList);

    const { result } = renderHook(() => useTheme());
    act(() => {
      result.current.setTheme("system");
    });
    expect(document.documentElement.dataset["theme"]).toBe("dark");
  });
});
