"use client";

import { useEffect, useState } from "react";
import type { JSX } from "react";

type ThemeMode = "light" | "dark" | "system";

const storageKey = "fitness-theme-preference";

function applyTheme(mode: ThemeMode): void {
  const root = document.documentElement;
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const resolved = mode === "system" ? (systemDark ? "dark" : "light") : mode;

  root.dataset["themeMode"] = mode;
  root.dataset["theme"] = resolved;
}

function ThemeModeIcon({ mode }: Readonly<{ mode: ThemeMode }>): JSX.Element {
  if (mode === "light") {
    return (
      <svg aria-hidden="true" className="theme-icon" viewBox="0 0 24 24">
        <circle cx="12" cy="12" fill="none" r="4.5" stroke="currentColor" strokeWidth="1.8" />
        <path d="M12 2.75v2.5M12 18.75v2.5M21.25 12h-2.5M5.25 12h-2.5M18.54 5.46l-1.77 1.77M7.23 16.77l-1.77 1.77M18.54 18.54l-1.77-1.77M7.23 7.23L5.46 5.46" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" />
      </svg>
    );
  }

  if (mode === "dark") {
    return (
      <svg aria-hidden="true" className="theme-icon" viewBox="0 0 24 24">
        <path d="M16.75 15.2A7.75 7.75 0 0 1 8.8 7.25 7.95 7.95 0 0 1 10 3.05 9.25 9.25 0 1 0 20.95 14a7.95 7.95 0 0 1-4.2 1.2Z" fill="none" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.8" />
      </svg>
    );
  }

  return (
    <svg aria-hidden="true" className="theme-icon" viewBox="0 0 24 24">
      <path d="M12 3.25a8.75 8.75 0 1 0 0 17.5Z" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path d="M12 3.25a8.75 8.75 0 0 1 0 17.5" fill="currentColor" opacity="0.2" />
    </svg>
  );
}

export function ThemeSwitcher(): JSX.Element {
  const [mode, setMode] = useState<ThemeMode>("system");

  useEffect(() => {
    const saved = window.localStorage.getItem(storageKey);
    const nextMode =
      saved === "light" || saved === "dark" || saved === "system" ? saved : "system";

    setMode(nextMode);
    applyTheme(nextMode);

    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const handleMediaChange = (): void => {
      const currentMode =
        (document.documentElement.dataset["themeMode"] as ThemeMode | undefined) ?? "system";
      if (currentMode === "system") {
        applyTheme("system");
      }
    };

    media.addEventListener("change", handleMediaChange);
    return (): void => {
      media.removeEventListener("change", handleMediaChange);
    };
  }, []);

  function handleSelect(nextMode: ThemeMode): void {
    setMode(nextMode);
    window.localStorage.setItem(storageKey, nextMode);
    applyTheme(nextMode);
  }

  return (
    <div aria-label="Theme selector" className="theme-toggle" role="group">
      {(["light", "dark", "system"] as const).map((entry) => (
        <button
          aria-label={`Use ${entry} theme`}
          aria-pressed={mode === entry}
          key={entry}
          onClick={() => handleSelect(entry)}
          type="button"
        >
          <ThemeModeIcon mode={entry} />
          <span>{entry.slice(0, 1).toUpperCase() + entry.slice(1)}</span>
        </button>
      ))}
    </div>
  );
}
