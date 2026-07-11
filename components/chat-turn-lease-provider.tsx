"use client";

import {
  createContext,
  type JSX,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { loadChatTurnLeaseStatus } from "../lib/coach-api";

const LEASE_STATUS_POLL_INTERVAL_MS = 750;

type ChatTurnLeaseContextValue = {
  clearPendingTurn: (_userId: string) => void;
  pendingUserIds: ReadonlySet<string>;
  startPendingTurn: (_userId: string) => void;
};

type ChatTurnLeaseProviderProps = Readonly<{
  children: ReactNode;
}>;

type ChatTurnLease = {
  releaseVersion: number;
  startTurn: () => void;
  turnInFlight: boolean;
};

const ChatTurnLeaseContext = createContext<ChatTurnLeaseContextValue | null>(
  null,
);

export function ChatTurnLeaseProvider({
  children,
}: ChatTurnLeaseProviderProps): JSX.Element {
  const [pendingUserIds, setPendingUserIds] = useState<ReadonlySet<string>>(
    new Set(),
  );
  const startPendingTurn = useCallback((userId: string): void => {
    setPendingUserIds((current) => new Set(current).add(userId));
  }, []);
  const clearPendingTurn = useCallback((userId: string): void => {
    setPendingUserIds((current) => {
      if (!current.has(userId)) return current;
      const next = new Set(current);
      next.delete(userId);
      return next;
    });
  }, []);
  const value = useMemo(
    () => ({ clearPendingTurn, pendingUserIds, startPendingTurn }),
    [clearPendingTurn, pendingUserIds, startPendingTurn],
  );

  return (
    <ChatTurnLeaseContext.Provider value={value}>
      {children}
    </ChatTurnLeaseContext.Provider>
  );
}

export function useChatTurnLease(userId: string): ChatTurnLease {
  const context = useContext(ChatTurnLeaseContext);
  const [leaseActive, setLeaseActive] = useState(false);
  const [releaseVersion, setReleaseVersion] = useState(0);
  const [statusKnown, setStatusKnown] = useState(context === null);
  const pendingTurn = context?.pendingUserIds.has(userId) ?? false;

  useEffect(() => {
    if (context === null) return;

    let cancelled = false;
    const refresh = async (): Promise<void> => {
      try {
        const status = await loadChatTurnLeaseStatus();
        if (cancelled) return;

        const hadTurnInFlight = pendingTurn || leaseActive;
        setLeaseActive(status.in_flight);
        setStatusKnown(true);
        if (!status.in_flight) {
          context.clearPendingTurn(userId);
          if (hadTurnInFlight) {
            setReleaseVersion((current) => current + 1);
          }
        }
      } catch {
        if (!cancelled) setStatusKnown(true);
      }
    };

    void refresh();
    const intervalId =
      pendingTurn || leaseActive
        ? window.setInterval(() => {
            void refresh();
          }, LEASE_STATUS_POLL_INTERVAL_MS)
        : null;

    return (): void => {
      cancelled = true;
      if (intervalId !== null) window.clearInterval(intervalId);
    };
  }, [context, leaseActive, pendingTurn, userId]);

  if (context === null) {
    return {
      releaseVersion: 0,
      startTurn: (): void => {},
      turnInFlight: false,
    };
  }

  return {
    releaseVersion,
    startTurn: (): void => {
      setStatusKnown(false);
      context.startPendingTurn(userId);
    },
    turnInFlight: !statusKnown || pendingTurn || leaseActive,
  };
}
