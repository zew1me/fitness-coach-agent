"use client";

import type { JSX } from "react";

import { useTheme } from "../lib/use-theme";
import type { ThemeMode } from "../lib/use-theme";

const themeModes: readonly ThemeMode[] = ["light", "dark", "system"];

function ThemeModeIcon({ mode }: Readonly<{ mode: ThemeMode }>): JSX.Element {
  if (mode === "light") {
    return (
      <svg aria-hidden="true" className="theme-icon" viewBox="0 0 24 24">
        <circle
          cx="12"
          cy="12"
          fill="none"
          r="4.5"
          stroke="currentColor"
          strokeWidth="1.8"
        />
        <path
          d="M12 2.75v2.5M12 18.75v2.5M21.25 12h-2.5M5.25 12h-2.5M18.54 5.46l-1.77 1.77M7.23 16.77l-1.77 1.77M18.54 18.54l-1.77-1.77M7.23 7.23L5.46 5.46"
          fill="none"
          stroke="currentColor"
          strokeLinecap="round"
          strokeWidth="1.8"
        />
      </svg>
    );
  }

  if (mode === "dark") {
    return (
      <svg aria-hidden="true" className="theme-icon" viewBox="0 0 24 24">
        <path
          d="M16.75 15.2A7.75 7.75 0 0 1 8.8 7.25 7.95 7.95 0 0 1 10 3.05 9.25 9.25 0 1 0 20.95 14a7.95 7.95 0 0 1-4.2 1.2Z"
          fill="none"
          stroke="currentColor"
          strokeLinejoin="round"
          strokeWidth="1.8"
        />
      </svg>
    );
  }

  return (
    <svg aria-hidden="true" className="theme-icon" viewBox="0 0 24 24">
      <path
        d="M12 3.25a8.75 8.75 0 1 0 0 17.5Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
      />
      <path
        d="M12 3.25a8.75 8.75 0 0 1 0 17.5"
        fill="currentColor"
        opacity="0.2"
      />
    </svg>
  );
}

function labelForMode(mode: ThemeMode): string {
  return mode.charAt(0).toUpperCase() + mode.slice(1);
}

export function ThemeSwitcher(): JSX.Element {
  const { mode, setTheme } = useTheme();

  return (
    <div aria-label="Theme selector" className="theme-toggle" role="group">
      {themeModes.map((entry) => (
        <button
          aria-label={`Use ${entry} theme`}
          aria-pressed={mode === entry}
          key={entry}
          onClick={() => setTheme(entry)}
          type="button"
        >
          <ThemeModeIcon mode={entry} />
          <span>{labelForMode(entry)}</span>
        </button>
      ))}
    </div>
  );
}
