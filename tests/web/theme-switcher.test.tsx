// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "../../components/app-shell";
import { ThemeSwitcher } from "../../components/theme-switcher";

const STORAGE_KEY = "fitness-theme-preference";

type LocalStorageMock = {
  clear(): void;
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
};

const localStorageMock: LocalStorageMock = ((): LocalStorageMock => {
  let store: Record<string, string> = {};
  return {
    clear(): void {
      store = {};
    },
    getItem(key: string): string | null {
      return store[key] ?? null;
    },
    setItem(key: string, value: string): void {
      store[key] = value;
    },
  };
})();

beforeEach(() => {
  vi.stubGlobal("React", React);
  vi.stubGlobal("localStorage", localStorageMock);
  localStorageMock.clear();
  document.documentElement.dataset["theme"] = "light";
  document.documentElement.dataset["themeMode"] = "system";
  vi.spyOn(window, "matchMedia").mockReturnValue({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  } as unknown as MediaQueryList);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ThemeSwitcher", () => {
  it("persists the selected theme and updates the root theme attributes", () => {
    render(<ThemeSwitcher />);

    fireEvent.click(screen.getByRole("button", { name: /use dark theme/i }));

    expect(localStorage.getItem(STORAGE_KEY)).toBe("dark");
    expect(document.documentElement.dataset["theme"]).toBe("dark");
    expect(document.documentElement.dataset["themeMode"]).toBe("dark");
    expect(
      screen
        .getByRole("button", { name: /use dark theme/i })
        .getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("renders in the app shell alongside page content", () => {
    render(
      <AppShell>
        <main>Training dashboard</main>
      </AppShell>,
    );

    expect(screen.getByText("Training dashboard")).toBeTruthy();
    expect(screen.getByRole("group", { name: /theme selector/i })).toBeTruthy();
  });
});
