"use client";

import {
  type InfiniteData,
  useInfiniteQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ZodError } from "zod";

import { loadChatMessages, loadChatThread } from "./coach-api";
import { errorMessage } from "./errors";
import type {
  BrowserTokenResponse,
  ChatMessage,
  ChatThreadResponse,
} from "./types";

const LOCAL_CHAT_THREAD_STORAGE_PREFIX = "fitness-coach.local-chat-thread";

type InitialThreadPage = {
  kind: "initial";
  thread: ChatThreadResponse;
};

type OlderThreadPage = {
  kind: "older";
  messages: ChatMessage[];
  next_cursor: string | null;
};

type ChatThreadPage = InitialThreadPage | OlderThreadPage;
type ChatThreadQueryData = InfiniteData<ChatThreadPage, string | null>;

type ManualRefreshOperation = {
  controller: AbortController;
  promise: Promise<void>;
  queryKey: readonly ["chat-thread", string | null];
};

function canUseLocalChatHistory(): boolean {
  return (
    window.location.hostname === "localhost" ||
    window.location.hostname === "127.0.0.1"
  );
}

function localChatThreadStorageKey(userId: string): string {
  return `${LOCAL_CHAT_THREAD_STORAGE_PREFIX}.${userId}`;
}

function chatThreadQueryKey(
  userId: string | null,
): readonly ["chat-thread", string | null] {
  return ["chat-thread", userId];
}

function firstInitialPage(
  data: ChatThreadQueryData | undefined,
): InitialThreadPage | null {
  if (data === undefined) {
    return null;
  }
  return (
    data.pages.find(
      (page): page is InitialThreadPage => page.kind === "initial",
    ) ?? null
  );
}

function mergeThreadPages(
  data: ChatThreadQueryData | undefined,
): ChatThreadResponse | null {
  const initialPage = firstInitialPage(data);
  if (data === undefined || initialPage === null) {
    return null;
  }

  const olderMessages = data.pages
    .filter((page): page is OlderThreadPage => page.kind === "older")
    .slice()
    .reverse()
    .flatMap((page) => page.messages);
  const lastPage = data.pages.at(-1);
  const nextCursor =
    lastPage?.kind === "older"
      ? lastPage.next_cursor
      : initialPage.thread.next_cursor;

  const mergedThread = {
    ...initialPage.thread,
    thread: {
      ...initialPage.thread.thread,
      messages: [...olderMessages, ...initialPage.thread.thread.messages],
    },
  };
  return nextCursor === undefined
    ? mergedThread
    : { ...mergedThread, next_cursor: nextCursor };
}

export function readLocalChatThread(userId: string): ChatThreadResponse | null {
  if (!canUseLocalChatHistory()) {
    return null;
  }

  try {
    const rawThread = window.localStorage.getItem(
      localChatThreadStorageKey(userId),
    );
    if (rawThread === null) {
      return null;
    }
    const parsed = JSON.parse(rawThread) as ChatThreadResponse;
    if (
      parsed.thread.user_id !== userId ||
      !Array.isArray(parsed.thread.messages)
    ) {
      return null;
    }
    return parsed;
  } catch (error) {
    console.warn("Discarding corrupt local chat thread cache", error);
    return null;
  }
}

export function writeLocalChatThread(
  thread: ChatThreadResponse,
  userId: string,
): void {
  if (!canUseLocalChatHistory()) {
    return;
  }

  try {
    window.localStorage.setItem(
      localChatThreadStorageKey(userId),
      JSON.stringify(thread),
    );
  } catch (error) {
    // Local persistence is best-effort for development and should never block
    // chat — log so QuotaExceededError doesn't disappear into the void.
    console.warn("Failed to persist local chat thread", error);
  }
}

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

export function useChatThread(
  token: BrowserTokenResponse | null,
): ChatThreadHook {
  const queryClient = useQueryClient();
  const userId = token?.user_id ?? null;
  const queryKey = useMemo(() => chatThreadQueryKey(userId), [userId]);
  const [manualError, setManualError] = useState<string | null>(null);
  const fetchingOlderRef = useRef(false);
  const manualRefreshRef = useRef<ManualRefreshOperation | null>(null);

  const cancelManualRefresh = useCallback((): void => {
    const operation = manualRefreshRef.current;
    if (operation === null) return;
    manualRefreshRef.current = null;
    operation.controller.abort();
  }, []);

  const query = useInfiniteQuery<
    ChatThreadPage,
    Error,
    ChatThreadQueryData,
    typeof queryKey,
    string | null
  >({
    enabled: token !== null,
    getNextPageParam: (lastPage) => {
      const nextCursor =
        lastPage.kind === "older"
          ? lastPage.next_cursor
          : lastPage.thread.next_cursor;
      return nextCursor ?? undefined;
    },
    initialPageParam: null,
    queryFn: async ({ pageParam, signal }) => {
      if (token === null) {
        throw new Error("Missing chat token.");
      }
      if (pageParam !== null) {
        const page = await loadChatMessages(pageParam, fetch, signal);
        return {
          kind: "older",
          messages: page.messages,
          next_cursor: page.next_cursor,
        };
      }
      try {
        return { kind: "initial", thread: await loadChatThread(fetch, signal) };
      } catch (error) {
        if (signal.aborted) {
          throw error;
        }
        // Schema violations mean the server returned bad data — re-throw so the
        // error surfaces rather than being silently masked by stale local cache.
        if (error instanceof ZodError) {
          throw error;
        }
        const localThread = readLocalChatThread(token.user_id);
        if (localThread !== null) {
          return { kind: "initial", thread: localThread };
        }
        throw error;
      }
    },
    queryKey,
  });

  const data = useMemo(() => mergeThreadPages(query.data), [query.data]);
  const error =
    manualError ??
    (query.error === null
      ? null
      : errorMessage(query.error, "Unable to load the coaching conversation."));

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

  useEffect(() => cancelManualRefresh, [cancelManualRefresh, queryKey]);

  const setData = useCallback(
    async (update: ChatThreadDataUpdate) => {
      cancelManualRefresh();
      await queryClient.cancelQueries({ queryKey });
      queryClient.setQueryData<ChatThreadQueryData>(queryKey, (current) => {
        const currentThread = mergeThreadPages(current);
        let nextThread: ChatThreadResponse;
        if (typeof update === "function") {
          if (currentThread === null) {
            return current;
          }
          nextThread = update(currentThread);
        } else {
          nextThread = update;
        }
        return {
          pageParams: [null],
          pages: [{ kind: "initial", thread: nextThread }],
        };
      });
      setManualError(null);
    },
    [cancelManualRefresh, queryClient, queryKey],
  );

  const setError = useCallback((nextError: string | null) => {
    setManualError(nextError);
  }, []);

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
        setManualError(null);
        await queryClient.cancelQueries({ queryKey });
        if (controller.signal.aborted) return;
        const thread = await loadChatThread(fetch, controller.signal);
        if (manualRefreshRef.current === operation) {
          queryClient.setQueryData<ChatThreadQueryData>(queryKey, {
            pageParams: [null],
            pages: [{ kind: "initial", thread }],
          });
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
  }, [cancelManualRefresh, queryClient, queryKey]);

  const fetchOlderMessages = useCallback(async (): Promise<number> => {
    if (fetchingOlderRef.current || !query.hasNextPage) {
      return 0;
    }
    fetchingOlderRef.current = true;
    const beforeCount = data?.thread.messages.length ?? 0;
    try {
      setManualError(null);
      const result = await query.fetchNextPage();
      const nextData = mergeThreadPages(result.data);
      return Math.max(0, (nextData?.thread.messages.length ?? 0) - beforeCount);
    } finally {
      fetchingOlderRef.current = false;
    }
  }, [data?.thread.messages.length, query]);

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
