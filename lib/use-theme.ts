"use client";

import { useCallback, useEffect, useState } from "react";

export type ThemeMode = "dark" | "light" | "system";

const STORAGE_KEY = "fitness-theme-preference";

function resolvedTheme(next: ThemeMode): "dark" | "light" {
  if (next !== "system") return next;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(next: ThemeMode): void {
  const resolved = resolvedTheme(next);
  document.documentElement.dataset["theme"] = resolved;
  document.documentElement.dataset["themeMode"] = next;
}

function readStoredMode(): ThemeMode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark" || saved === "system") return saved;
  } catch {
    // localStorage unavailable (SSR, private browsing)
  }
  return "system";
}

export function useTheme(): { mode: ThemeMode; setTheme: (_theme: ThemeMode) => void } {
  const [mode, setMode] = useState<ThemeMode>("system");

  useEffect(() => {
    const stored = readStoredMode();
    setMode(stored);
    applyTheme(stored);
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
