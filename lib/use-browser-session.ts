"use client";

import * as Sentry from "@sentry/nextjs";
import { useCallback, useEffect, useRef, useState } from "react";

import { fetchBrowserToken, isAuthenticationError } from "./coach-api";
import { errorMessage } from "./errors";
import type { BrowserTokenResponse } from "./types";

const SESSION_REFRESH_LEAD_MS = 60_000;
const SESSION_REFRESH_MIN_DELAY_MS = 5_000;
const SESSION_REFRESH_MAX_DELAY_MS = 10 * 60_000;
const SESSION_REFRESH_RETRY_DELAYS_MS = [
  30_000,
  2 * 60_000,
  5 * 60_000,
] as const;

export type BrowserSessionState = {
  authenticationRequired: boolean;
  error: string | null;
  loading: boolean;
  refresh: () => Promise<void>;
  token: BrowserTokenResponse | null;
};

type BrowserSessionInternalState = Omit<BrowserSessionState, "refresh"> & {
  renewalRetryAttempt: number;
};

function sessionRefreshDelay(expiresAt: string): number {
  const remainingMs = Date.parse(expiresAt) - Date.now();
  if (!Number.isFinite(remainingMs)) return SESSION_REFRESH_MAX_DELAY_MS;
  return Math.max(
    SESSION_REFRESH_MIN_DELAY_MS,
    Math.min(
      remainingMs - SESSION_REFRESH_LEAD_MS,
      SESSION_REFRESH_MAX_DELAY_MS,
    ),
  );
}

export function useBrowserSession(): BrowserSessionState {
  const [state, setState] = useState<BrowserSessionInternalState>({
    authenticationRequired: false,
    token: null,
    error: null,
    loading: true,
    renewalRetryAttempt: 0,
  });
  const mountedRef = useRef(true);
  const establishedRef = useRef(false);
  const refreshInFlightRef = useRef<Promise<void> | null>(null);

  const refresh = useCallback((): Promise<void> => {
    const activeRefresh = refreshInFlightRef.current;
    if (activeRefresh !== null) return activeRefresh;

    const operation = (async (): Promise<void> => {
      try {
        const token = await fetchBrowserToken();
        if (!mountedRef.current) return;
        if (establishedRef.current) {
          Sentry.logger.debug("browser session renewed", {
            user_id: token.user_id,
          });
        } else {
          Sentry.logger.info("browser session established", {
            user_id: token.user_id,
          });
          establishedRef.current = true;
        }
        Sentry.setUser({ id: token.user_id });
        setState({
          authenticationRequired: false,
          token,
          error: null,
          loading: false,
          renewalRetryAttempt: 0,
        });
      } catch (error) {
        if (!mountedRef.current) return;
        const authenticationRequired = isAuthenticationError(error);
        Sentry.logger.warn("browser session refresh failed", {
          authentication_required: authenticationRequired,
          reason: errorMessage(error, "unknown"),
        });
        if (authenticationRequired) {
          Sentry.setUser(null);
          setState({
            authenticationRequired: true,
            token: null,
            // The reauthentication screen owns this copy; carrying a parallel
            // message here would duplicate it and leak the raw 401 detail.
            error: null,
            loading: false,
            renewalRetryAttempt: 0,
          });
          return;
        }
        setState((current) => {
          // A transient background failure should not throw an athlete out of
          // an otherwise valid session. Schedule a bounded retry series; focus
          // and visibility events remain available after those retries are
          // exhausted. Bootstrap failures still need a visible fallback because
          // there is no authenticated UI to preserve.
          if (current.token !== null) {
            if (
              current.renewalRetryAttempt >=
              SESSION_REFRESH_RETRY_DELAYS_MS.length
            ) {
              return current;
            }
            return {
              ...current,
              renewalRetryAttempt: current.renewalRetryAttempt + 1,
            };
          }
          Sentry.setUser(null);
          return {
            authenticationRequired: false,
            token: null,
            error: errorMessage(
              error,
              "Unable to connect your browser session.",
            ),
            loading: false,
            renewalRetryAttempt: 0,
          };
        });
      }
    })();

    refreshInFlightRef.current = operation;
    void operation.finally(() => {
      if (refreshInFlightRef.current === operation) {
        refreshInFlightRef.current = null;
      }
    });
    return operation;
  }, []);

  useEffect(() => {
    // React Strict Mode replays effect setup after cleanup in development.
    mountedRef.current = true;
    void refresh();
    return (): void => {
      mountedRef.current = false;
    };
  }, [refresh]);

  useEffect((): (() => void) | void => {
    if (state.token === null) return;

    const retryDelay =
      state.renewalRetryAttempt === 0
        ? null
        : SESSION_REFRESH_RETRY_DELAYS_MS[state.renewalRetryAttempt - 1];
    const timerId = window.setTimeout(
      () => {
        void refresh();
      },
      retryDelay ?? sessionRefreshDelay(state.token.expires_at),
    );
    const refreshOnFocus = (): void => {
      void refresh();
    };
    const refreshWhenVisible = (): void => {
      if (document.visibilityState === "visible") {
        void refresh();
      }
    };
    window.addEventListener("focus", refreshOnFocus);
    document.addEventListener("visibilitychange", refreshWhenVisible);

    return (): void => {
      window.clearTimeout(timerId);
      window.removeEventListener("focus", refreshOnFocus);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [refresh, state.renewalRetryAttempt, state.token]);

  return {
    authenticationRequired: state.authenticationRequired,
    error: state.error,
    loading: state.loading,
    refresh,
    token: state.token,
  };
}
