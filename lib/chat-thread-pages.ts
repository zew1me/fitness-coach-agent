import type { InfiniteData } from "@tanstack/react-query";
import { ZodError } from "zod";

import { loadChatMessages, loadChatThread } from "./coach-api";
import { readLocalChatThread } from "./local-chat-thread";
import type { ChatMessage, ChatThreadResponse } from "./types";

type InitialThreadPage = {
  kind: "initial";
  thread: ChatThreadResponse;
};

type OlderThreadPage = {
  kind: "older";
  messages: ChatMessage[];
  next_cursor: string | null;
};

export type ChatThreadPage = InitialThreadPage | OlderThreadPage;
export type ChatThreadQueryData = InfiniteData<ChatThreadPage, string | null>;
export type ChatThreadQueryKey = readonly ["chat-thread", string | null];

export function chatThreadQueryKey(userId: string | null): ChatThreadQueryKey {
  return ["chat-thread", userId];
}

/** Query data holding exactly one authoritative initial page. */
export function singleInitialPageData(
  thread: ChatThreadResponse,
): ChatThreadQueryData {
  return { pageParams: [null], pages: [{ kind: "initial", thread }] };
}

export function nextThreadCursor(lastPage: ChatThreadPage): string | undefined {
  const nextCursor =
    lastPage.kind === "older"
      ? lastPage.next_cursor
      : lastPage.thread.next_cursor;
  return nextCursor ?? undefined;
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

export function mergeThreadPages(
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

async function loadInitialPage(
  userId: string,
  signal: AbortSignal,
): Promise<ChatThreadPage> {
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
    const localThread = readLocalChatThread(userId);
    if (localThread !== null) {
      return { kind: "initial", thread: localThread };
    }
    throw error;
  }
}

/** Fetches one page: the initial thread (null cursor) or an older-messages page. */
export async function loadThreadPage(
  userId: string,
  pageParam: string | null,
  signal: AbortSignal,
): Promise<ChatThreadPage> {
  if (pageParam === null) {
    return loadInitialPage(userId, signal);
  }
  const page = await loadChatMessages(pageParam, fetch, signal);
  return {
    kind: "older",
    messages: page.messages,
    next_cursor: page.next_cursor,
  };
}
