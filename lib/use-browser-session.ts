"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect, useState } from "react";

import { fetchBrowserToken } from "./coach-api";
import { errorMessage } from "./errors";
import type { BrowserTokenResponse } from "./types";

export type BrowserSessionState = {
  error: string | null;
  loading: boolean;
  token: BrowserTokenResponse | null;
};

export function useBrowserSession(): BrowserSessionState {
  const [state, setState] = useState<BrowserSessionState>({
    token: null,
    error: null,
    loading: true,
  });

  useEffect(() => {
    let cancelled = false;
    async function bootstrap(): Promise<void> {
      try {
        const token = await fetchBrowserToken();
        if (cancelled) return;
        Sentry.logger.info("browser session established", {
          user_id: token.user_id,
        });
        Sentry.setUser({ id: token.user_id });
        setState({ token, error: null, loading: false });
      } catch (error) {
        if (cancelled) return;
        Sentry.setUser(null);
        Sentry.logger.warn("browser session failed", {
          reason: errorMessage(error, "unknown"),
        });
        setState({
          token: null,
          error: errorMessage(error, "Unable to connect your browser session."),
          loading: false,
        });
      }
    }

    void bootstrap();
    return (): void => {
      cancelled = true;
    };
  }, []);

  return state;
}
