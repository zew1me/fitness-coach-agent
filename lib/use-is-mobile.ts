"use client";

import { useEffect, useState } from "react";

const MOBILE_QUERY = "(max-width: 760px)";

/**
 * Tracks whether the viewport matches the "(max-width: 760px)" media query and updates as the viewport changes.
 *
 * @returns `true` if the viewport width is 760px or less, `false` otherwise. In environments without a `window` (for example during server-side rendering) this will be `false` until a client-side value is available.
 */
export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.matchMedia(MOBILE_QUERY).matches;
  });

  useEffect(() => {
    const mql = window.matchMedia(MOBILE_QUERY);
    setIsMobile(mql.matches);
    const handler = (event: MediaQueryListEvent): void => {
      setIsMobile(event.matches);
    };
    mql.addEventListener("change", handler);
    return (): void => {
      mql.removeEventListener("change", handler);
    };
  }, []);

  return isMobile;
}
