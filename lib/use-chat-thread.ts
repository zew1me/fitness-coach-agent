"use client";

import {
  useInfiniteQuery,
  type UseInfiniteQueryResult,
  useQueryClient,
} from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  type ChatThreadPage,
  type ChatThreadQueryData,
  chatThreadQueryKey,
  loadThreadPage,
  mergeThreadPages,
  nextThreadCursor,
  singleInitialPageData,
} from "./chat-thread-pages";
import { errorMessage } from "./errors";
import { writeLocalChatThread } from "./local-chat-thread";
import type { BrowserTokenResponse, ChatThreadResponse } from "./types";
import { useManualChatRefresh } from "./use-manual-chat-refresh";

export type ChatThreadState = {
  data: ChatThreadResponse | null;
  error: string | null;
  loading: boolean;
};

type ChatThreadDataUpdate =
  | ChatThreadResponse
  | ((_current: ChatThreadResponse) => ChatThreadResponse);

export type ChatThreadHook = ChatThreadState & {
  olderAvailable: boolean;
  loadingOlder: boolean;
  fetchOlderMessages: () => Promise<number>;
  refetch: () => Promise<void>;
  setData: (_update: ChatThreadDataUpdate) => Promise<void>;
  setError: (_error: string | null) => void;
};

function queryErrorMessage(error: Error | null): string | null {
  return error === null
    ? null
    : errorMessage(error, "Unable to load the coaching conversation.");
}

/** Replaces the cached pages with one initial page; function updaters need cached data. */
function applyThreadUpdate(
  current: ChatThreadQueryData | undefined,
  update: ChatThreadDataUpdate,
): ChatThreadQueryData | undefined {
  if (typeof update !== "function") {
    return singleInitialPageData(update);
  }
  const currentThread = mergeThreadPages(current);
  return currentThread === null
    ? current
    : singleInitialPageData(update(currentThread));
}

/** Loads one older page, returning how many messages it added (0 while one is in flight). */
function useFetchOlderMessages(
  query: UseInfiniteQueryResult<ChatThreadQueryData, Error>,
  data: ChatThreadResponse | null,
  clearError: () => void,
): () => Promise<number> {
  const fetchingOlderRef = useRef(false);
  return useCallback(async (): Promise<number> => {
    if (fetchingOlderRef.current || !query.hasNextPage) {
      return 0;
    }
    fetchingOlderRef.current = true;
    const beforeCount = data?.thread.messages.length ?? 0;
    try {
      clearError();
      const result = await query.fetchNextPage();
      const nextData = mergeThreadPages(result.data);
      return Math.max(0, (nextData?.thread.messages.length ?? 0) - beforeCount);
    } finally {
      fetchingOlderRef.current = false;
    }
  }, [clearError, data?.thread.messages.length, query]);
}

/**
 * Mirrors the loaded thread into local storage (dev only, best-effort).
 */
function usePersistLocalThread(
  token: BrowserTokenResponse | null,
  data: ChatThreadResponse | null,
): void {
  useEffect(() => {
    if (token === null || data === null || data.thread.messages.length === 0) {
      return;
    }
    // Guard against a token swap landing while the previous user's thread is
    // still in cache — without this, the in-flight write would stamp user A's
    // history under user B's storage key.
    if (data.thread.user_id !== token.user_id) {
      return;
    }
    writeLocalChatThread(data, token.user_id);
  }, [data, token]);
}

export function useChatThread(
  token: BrowserTokenResponse | null,
): ChatThreadHook {
  const queryClient = useQueryClient();
  const userId = token?.user_id ?? null;
  const queryKey = useMemo(() => chatThreadQueryKey(userId), [userId]);
  const [manualError, setManualError] = useState<string | null>(null);
  const clearManualError = useCallback(() => setManualError(null), []);
  const { cancelManualRefresh, refetch } = useManualChatRefresh(
    queryKey,
    clearManualError,
  );

  const query = useInfiniteQuery<
    ChatThreadPage,
    Error,
    ChatThreadQueryData,
    typeof queryKey,
    string | null
  >({
    enabled: token !== null,
    getNextPageParam: nextThreadCursor,
    initialPageParam: null,
    queryFn: ({ pageParam, signal }) => {
      if (token === null) {
        throw new Error("Missing chat token.");
      }
      return loadThreadPage(token.user_id, pageParam, signal);
    },
    queryKey,
  });

  const data = useMemo(() => mergeThreadPages(query.data), [query.data]);
  const error = manualError ?? queryErrorMessage(query.error);

  usePersistLocalThread(token, data);

  const setData = useCallback(
    async (update: ChatThreadDataUpdate) => {
      cancelManualRefresh();
      await queryClient.cancelQueries({ queryKey });
      queryClient.setQueryData<ChatThreadQueryData>(queryKey, (current) =>
        applyThreadUpdate(current, update),
      );
      setManualError(null);
    },
    [cancelManualRefresh, queryClient, queryKey],
  );

  const setError = useCallback((nextError: string | null) => {
    setManualError(nextError);
  }, []);

  const fetchOlderMessages = useFetchOlderMessages(
    query,
    data,
    clearManualError,
  );

  return {
    data,
    error,
    fetchOlderMessages,
    loading: token !== null && query.isPending,
    loadingOlder: query.isFetchingNextPage,
    olderAvailable: query.hasNextPage,
    refetch,
    setData,
    setError,
  };
}
