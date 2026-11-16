"use client";

import { useSyncExternalStore } from "react";

const MOBILE_QUERY = "(max-width: 760px)";

function subscribe(onChange: () => void): () => void {
  const mql = window.matchMedia(MOBILE_QUERY);
  mql.addEventListener("change", onChange);
  return (): void => {
    mql.removeEventListener("change", onChange);
  };
}

function getSnapshot(): boolean {
  return window.matchMedia(MOBILE_QUERY).matches;
}

// The server has no viewport, so it renders the desktop branch. React uses
// this value for the hydration render too, then switches to getSnapshot, so a
// phone's first client render matches the server markup instead of tripping a
// hydration mismatch.
function getServerSnapshot(): boolean {
  return false;
}

/**
 * Tracks whether the viewport matches the "(max-width: 760px)" media query and updates as the viewport changes.
 *
 * @returns `true` if the viewport width is 760px or less, `false` otherwise. During server rendering and hydration this is `false`, then the client value takes over.
 */
export function useIsMobile(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
