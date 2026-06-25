// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "../../components/app-shell";
import { ThemeSwitcher } from "../../components/theme-switcher";

import { createLocalStorageMock } from "./test-utils";

const STORAGE_KEY = "fitness-theme-preference";

const localStorageMock = createLocalStorageMock();

beforeEach(() => {
  // Components use the classic JSX runtime under vitest and reference a global
  // `React`, so this stub is required despite CodeRabbit flagging it as unused.
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

  it("renders page content without injecting a floating switcher", () => {
    render(
      <AppShell>
        <main>Training dashboard</main>
      </AppShell>,
    );

    expect(screen.getByText("Training dashboard")).toBeTruthy();
    // The switcher now lives in the account flyout, not the global shell.
    expect(screen.queryByRole("group", { name: /theme selector/i })).toBeNull();
  });
});
