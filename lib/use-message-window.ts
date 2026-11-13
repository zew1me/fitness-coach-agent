import { useEffect, useRef, useState } from "react";
import type { Dispatch, RefObject, SetStateAction } from "react";

import { errorMessage } from "./errors";
import type { ChatMessage } from "./types";

export const MESSAGE_RENDER_BATCH_SIZE = 60;

type MessageWindowOptions = {
  messages: ChatMessage[];
  persistedMessageCount: number;
  sending: boolean;
  threadId: string;
  /** Bumped by any thread refresh so a superseded older-page load is ignored. */
  olderRequestVersionRef: RefObject<number>;
  fetchOlderMessages: () => Promise<number>;
  loadingOlder: boolean;
  olderAvailable: boolean;
  setThreadError: (_error: string | null) => void;
};

type VisibleWindow = { threadId: string; count: number };

type MessageWindow = {
  messageEndRef: RefObject<HTMLDivElement | null>;
  hiddenMessageCount: number;
  visibleMessages: ChatMessage[];
  showMore: () => void;
  resetWindow: () => void;
};

function loadOlderMessages({
  olderRequestVersionRef,
  fetchOlderMessages,
  setThreadError,
  setVisibleMessageCount,
}: Pick<
  MessageWindowOptions,
  "fetchOlderMessages" | "olderRequestVersionRef" | "setThreadError"
> & {
  setVisibleMessageCount: Dispatch<SetStateAction<number>>;
}): void {
  const requestVersion = olderRequestVersionRef.current;
  void fetchOlderMessages()
    .then((addedCount) => {
      if (olderRequestVersionRef.current !== requestVersion) return;
      if (addedCount > 0) {
        setVisibleMessageCount((visibleCount) => visibleCount + addedCount);
      }
    })
    .catch((error) => {
      if (olderRequestVersionRef.current !== requestVersion) return;
      setThreadError(errorMessage(error, "Unable to load older messages."));
    })
    .finally(() => {
      if (olderRequestVersionRef.current === requestVersion) {
        olderRequestVersionRef.current += 1;
      }
    });
}

/**
 * Owns the rendered slice of the transcript: how many of the newest messages
 * are mounted, revealing (or paging in) older ones, and keeping the newest
 * message scrolled into view.
 */
export function useMessageWindow({
  messages,
  persistedMessageCount,
  sending,
  threadId,
  olderRequestVersionRef,
  fetchOlderMessages,
  loadingOlder,
  olderAvailable,
  setThreadError,
}: MessageWindowOptions): MessageWindow {
  const messageEndRef = useRef<HTMLDivElement | null>(null);
  // The count is stored with the thread it belongs to. When the thread changes,
  // the stored window is replaced during render (React re-runs the render before
  // committing, so no frame shows the old count), instead of an effect rendering
  // the new thread with the old count first and then correcting it. Replacing it,
  // rather than only reading the default for a foreign thread, also means
  // returning to an earlier thread starts from the default batch again.
  const [visibleWindow, setVisibleWindow] = useState<VisibleWindow>({
    threadId,
    count: MESSAGE_RENDER_BATCH_SIZE,
  });
  if (visibleWindow.threadId !== threadId) {
    setVisibleWindow({ threadId, count: MESSAGE_RENDER_BATCH_SIZE });
  }
  const visibleMessageCount =
    visibleWindow.threadId === threadId
      ? visibleWindow.count
      : MESSAGE_RENDER_BATCH_SIZE;
  const setVisibleMessageCount: Dispatch<SetStateAction<number>> = (action) => {
    setVisibleWindow((current) => {
      const currentCount =
        current.threadId === threadId
          ? current.count
          : MESSAGE_RENDER_BATCH_SIZE;
      return {
        threadId,
        count: typeof action === "function" ? action(currentCount) : action,
      };
    });
  };

  useEffect(() => {
    const scrollTarget = messageEndRef.current;
    if (
      scrollTarget !== null &&
      typeof scrollTarget.scrollIntoView === "function"
    ) {
      scrollTarget.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [persistedMessageCount, sending]);

  const hiddenMessageCount = Math.max(0, messages.length - visibleMessageCount);
  const visibleMessages =
    hiddenMessageCount > 0 ? messages.slice(hiddenMessageCount) : messages;

  function showMore(): void {
    if (hiddenMessageCount > 0) {
      setVisibleMessageCount((current) => current + MESSAGE_RENDER_BATCH_SIZE);
      return;
    }
    if (!olderAvailable || loadingOlder) {
      return;
    }
    loadOlderMessages({
      fetchOlderMessages,
      olderRequestVersionRef,
      setThreadError,
      setVisibleMessageCount,
    });
  }

  function resetWindow(): void {
    setVisibleMessageCount(MESSAGE_RENDER_BATCH_SIZE);
  }

  return {
    hiddenMessageCount,
    messageEndRef,
    resetWindow,
    showMore,
    visibleMessages,
  };
}
