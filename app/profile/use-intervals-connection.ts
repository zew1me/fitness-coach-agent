import { useCallback, useState } from "react";

import {
  disconnectIntervals,
  loadIntervalsStatus,
  startIntervalsAuthorization,
  syncIntervals,
} from "../../lib/coach-api";
import type { IntervalsConnectionStatus } from "../../lib/types";

export type IntervalsAction = "connect" | "disconnect" | "sync";

type IntervalsConnection = {
  action: IntervalsAction | null;
  connect: () => void;
  disconnect: () => void;
  error: string | null;
  loadStatus: (isCancelled: () => boolean) => Promise<void>;
  notice: string | null;
  status: IntervalsConnectionStatus | null;
  sync: () => void;
  consumeReturnParam: () => void;
};

/** Intervals.icu connection state, its actions, and the OAuth-return banner. */
export function useIntervalsConnection(): IntervalsConnection {
  const [status, setStatus] = useState<IntervalsConnectionStatus | null>(null);
  const [action, setAction] = useState<IntervalsAction | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const consumeReturnParam = useCallback((): void => {
    const result = new URLSearchParams(window.location.search).get("intervals");
    if (result === "connected") {
      setNotice("Intervals.icu connected.");
    } else if (result === "error") {
      setError("Intervals.icu authorization was not completed.");
    }
    if (result !== null) {
      const nextUrl = new URL(window.location.href);
      nextUrl.searchParams.delete("intervals");
      window.history.replaceState(
        {},
        "",
        `${nextUrl.pathname}${nextUrl.search}${nextUrl.hash}`,
      );
    }
  }, []);

  const loadStatus = useCallback(
    async (isCancelled: () => boolean): Promise<void> => {
      try {
        const nextStatus = await loadIntervalsStatus();
        if (!isCancelled()) {
          setStatus(nextStatus);
        }
      } catch (err: unknown) {
        if (!isCancelled()) {
          setStatus({ connected: false, scopes: [] });
          setError(
            err instanceof Error
              ? err.message
              : "Failed to load Intervals.icu connection status.",
          );
        }
      }
    },
    [],
  );

  const connect = useCallback((): void => {
    setAction("connect");
    setError(null);
    startIntervalsAuthorization()
      .then((redirectUrl) => {
        window.location.assign(redirectUrl);
      })
      .catch((err: unknown) => {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to start Intervals.icu authorization.",
        );
      })
      .finally((): void => {
        setAction(null);
      });
  }, []);

  const disconnect = useCallback((): void => {
    setAction("disconnect");
    setError(null);
    setNotice(null);
    disconnectIntervals()
      .then(setStatus)
      .catch((err: unknown) => {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to disconnect Intervals.icu.",
        );
      })
      .finally((): void => {
        setAction(null);
      });
  }, []);

  const sync = useCallback((): void => {
    setAction("sync");
    setError(null);
    setNotice(null);
    syncIntervals()
      .then(({ skipped_duplicates, skipped_invalid, synced }) => {
        const details = [`${skipped_duplicates} already imported`];
        if (skipped_invalid > 0) {
          details.push(`${skipped_invalid} couldn't be imported`);
        }
        setNotice(`Synced ${synced} (${details.join("; ")}).`);
      })
      .catch((err: unknown) => {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to sync Intervals.icu activities.",
        );
      })
      .finally((): void => {
        setAction(null);
      });
  }, []);

  return {
    action,
    connect,
    consumeReturnParam,
    disconnect,
    error,
    loadStatus,
    notice,
    status,
    sync,
  };
}
