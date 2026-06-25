"use client";

import { useCallback, useEffect, useState } from "react";

export type ThemeMode = "dark" | "light" | "system";

const STORAGE_KEY = "fitness-theme-preference";

function resolvedTheme(next: ThemeMode): "dark" | "light" {
  if (next !== "system") return next;
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function applyResolvedTheme(next: ThemeMode, resolved: "dark" | "light"): void {
  document.documentElement.dataset["theme"] = resolved;
  document.documentElement.dataset["themeMode"] = next;
}

function applyTheme(next: ThemeMode): void {
  applyResolvedTheme(next, resolvedTheme(next));
}

function readStoredMode(): ThemeMode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark" || saved === "system")
      return saved;
  } catch {
    // localStorage unavailable (SSR, private browsing)
  }
  return "system";
}

export function useTheme(): {
  mode: ThemeMode;
  setTheme: (_theme: ThemeMode) => void;
} {
  const [mode, setMode] = useState<ThemeMode>("system");

  useEffect(() => {
    const stored = readStoredMode();
    setMode(stored);
    applyTheme(stored);

    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const handleMediaChange = (event: MediaQueryListEvent): void => {
      if (document.documentElement.dataset["themeMode"] === "system") {
        applyResolvedTheme("system", event.matches ? "dark" : "light");
      }
    };

    media.addEventListener("change", handleMediaChange);
    return (): void => {
      media.removeEventListener("change", handleMediaChange);
    };
  }, []);

  const setTheme = useCallback((theme: ThemeMode) => {
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // ignore
    }
    setMode(theme);
    applyTheme(theme);
  }, []);

  return { mode, setTheme };
}
