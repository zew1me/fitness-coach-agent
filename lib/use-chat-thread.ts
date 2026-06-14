"use client";

import { useCallback, useEffect, useState } from "react";

import { loadChatThread } from "./coach-api";
import { errorMessage } from "./errors";
import type { BrowserTokenResponse, ChatThreadResponse } from "./types";

const LOCAL_CHAT_THREAD_STORAGE_PREFIX = "fitness-coach.local-chat-thread";

function canUseLocalChatHistory(): boolean {
  return (
    window.location.hostname === "localhost" ||
    window.location.hostname === "127.0.0.1"
  );
}

function localChatThreadStorageKey(userId: string): string {
  return `${LOCAL_CHAT_THREAD_STORAGE_PREFIX}.${userId}`;
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
  } catch {
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
  } catch {
    // Local persistence is best-effort for development and should never block chat.
  }
}

export type ChatThreadState = {
  data: ChatThreadResponse | null;
  error: string | null;
  loading: boolean;
};

export type ChatThreadHook = ChatThreadState & {
  setData: (_thread: ChatThreadResponse) => void;
  setError: (_error: string | null) => void;
};

export function useChatThread(
  token: BrowserTokenResponse | null,
): ChatThreadHook {
  const [state, setState] = useState<ChatThreadState>({
    data: null,
    error: null,
    loading: false,
  });

  useEffect(() => {
    if (token === null) {
      return;
    }
    const activeToken = token;
    let cancelled = false;

    async function loadThread(): Promise<void> {
      setState((current) => ({ ...current, loading: true, error: null }));
      try {
        const thread = await loadChatThread();
        if (cancelled) return;
        setState({ data: thread, error: null, loading: false });
      } catch (error) {
        if (cancelled) return;
        const localThread = readLocalChatThread(activeToken.user_id);
        if (localThread !== null) {
          setState({ data: localThread, error: null, loading: false });
          return;
        }
        setState({
          data: null,
          error: errorMessage(
            error,
            "Unable to load the coaching conversation.",
          ),
          loading: false,
        });
      }
    }

    void loadThread();
    return (): void => {
      cancelled = true;
    };
  }, [token]);

  useEffect(() => {
    if (
      token === null ||
      state.data === null ||
      state.data.thread.messages.length === 0
    ) {
      return;
    }
    writeLocalChatThread(state.data, token.user_id);
  }, [token, state.data]);

  const setData = useCallback((thread: ChatThreadResponse) => {
    setState({ data: thread, error: null, loading: false });
  }, []);

  const setError = useCallback((error: string | null) => {
    setState((current) => ({ ...current, error, loading: false }));
  }, []);

  return { ...state, setData, setError };
}
