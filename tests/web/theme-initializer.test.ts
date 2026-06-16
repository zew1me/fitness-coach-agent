// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { themeInitializer } from "../../lib/theme-initializer";

beforeEach(() => {
  document.documentElement.dataset["theme"] = "light";
  document.documentElement.dataset["themeMode"] = "system";
  vi.spyOn(window, "matchMedia").mockReturnValue({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  } as unknown as MediaQueryList);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("themeInitializer", () => {
  it("falls back to system mode when localStorage is unavailable", () => {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: {
        getItem: vi.fn(() => {
          throw new Error("storage unavailable");
        }),
      },
    });

    expect(() => {
      new Function(themeInitializer)();
    }).not.toThrow();

    expect(document.documentElement.dataset["theme"]).toBe("light");
    expect(document.documentElement.dataset["themeMode"]).toBe("system");
  });
});
