import type { ChatThreadResponse } from "./types";

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
