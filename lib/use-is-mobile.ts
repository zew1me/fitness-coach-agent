"use client";

import { useEffect, useState } from "react";

const MOBILE_QUERY = "(max-width: 760px)";

export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(false);

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
