import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef } from "react";

import {
  type ChatThreadQueryData,
  type ChatThreadQueryKey,
  singleInitialPageData,
} from "./chat-thread-pages";
import { loadChatThread } from "./coach-api";

type ManualRefreshOperation = {
  controller: AbortController;
  promise: Promise<void>;
  queryKey: ChatThreadQueryKey;
};

/**
 * Owns the manual refresh lifecycle (turn-lease navigation, #347/#349): one
 * shared in-flight refresh per query key, aborted on unmount, key change, or
 * when authoritative data supersedes it via `cancelManualRefresh`.
 */
export function useManualChatRefresh(
  queryKey: ChatThreadQueryKey,
  clearError: () => void,
): { cancelManualRefresh: () => void; refetch: () => Promise<void> } {
  const queryClient = useQueryClient();
  const manualRefreshRef = useRef<ManualRefreshOperation | null>(null);

  const cancelManualRefresh = useCallback((): void => {
    const operation = manualRefreshRef.current;
    if (operation === null) return;
    manualRefreshRef.current = null;
    operation.controller.abort();
  }, []);

  useEffect(() => cancelManualRefresh, [cancelManualRefresh, queryKey]);

  const refetch = useCallback((): Promise<void> => {
    const activeOperation = manualRefreshRef.current;
    if (activeOperation?.queryKey === queryKey) {
      return activeOperation.promise;
    }

    cancelManualRefresh();
    const controller = new AbortController();
    let operation!: ManualRefreshOperation;
    const promise = (async (): Promise<void> => {
      try {
        clearError();
        await queryClient.cancelQueries({ queryKey });
        if (controller.signal.aborted) return;
        const thread = await loadChatThread(fetch, controller.signal);
        if (manualRefreshRef.current === operation) {
          queryClient.setQueryData<ChatThreadQueryData>(
            queryKey,
            singleInitialPageData(thread),
          );
        }
      } catch (error) {
        if (!controller.signal.aborted) throw error;
      } finally {
        if (manualRefreshRef.current === operation) {
          manualRefreshRef.current = null;
        }
      }
    })();
    operation = { controller, promise, queryKey };
    manualRefreshRef.current = operation;
    // The operation may outlive every caller during unmount or a token swap.
    // Mark its rejection as observed while preserving the original promise for
    // active callers, which still receive genuine refresh failures.
    void promise.catch(() => undefined);
    return promise;
  }, [cancelManualRefresh, clearError, queryClient, queryKey]);

  return { cancelManualRefresh, refetch };
}
