import { useEffect, useState } from "react";

const WAITING_STATUS_INTERVAL_MS = 1600;
const WAITING_STATUSES = [
  "Thinking...",
  "Still working...",
  "Checking the coaching notes...",
  "Almost there...",
];

/** Cycles the composer's waiting copy while a turn is sending. */
export function useWaitingStatus(sending: boolean): string | undefined {
  const [waitingStatusIndex, setWaitingStatusIndex] = useState(0);

  useEffect((): (() => void) | void => {
    if (!sending) {
      setWaitingStatusIndex(0);
      return;
    }
    const intervalId = window.setInterval(() => {
      setWaitingStatusIndex(
        (current) => (current + 1) % WAITING_STATUSES.length,
      );
    }, WAITING_STATUS_INTERVAL_MS);
    return (): void => {
      window.clearInterval(intervalId);
    };
  }, [sending]);

  return WAITING_STATUSES[waitingStatusIndex];
}
