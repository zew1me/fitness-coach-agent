import { useChat, type UseChatHelpers } from "@ai-sdk/react";
import { DefaultChatTransport, type UIMessage } from "ai";
import { useEffect, useMemo, useRef } from "react";

import { toLiveChatMessage, toUiMessage } from "./chat-ui-messages";
import type { ChatMessage } from "./types";

type LiveChatMessages = Pick<
  UseChatHelpers<UIMessage>,
  "sendMessage" | "setMessages"
> & {
  displayedMessages: ChatMessage[];
};

function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

/**
 * Bridges the persisted thread into the AI SDK chat and merges the SDK's live
 * (streaming or optimistic) messages back onto the persisted rows.
 */
export function useLiveChatMessages(
  persistedMessages: ChatMessage[],
  threadId: string,
  userId: string,
): LiveChatMessages {
  const knownPersistedMessageIdsRef = useRef<Set<string>>(new Set());
  const chatMessages = useMemo<UIMessage[]>(
    () => persistedMessages.map(toUiMessage),
    [persistedMessages],
  );
  // useChat keeps its own initial message state after thread refreshes. Track
  // loaded DB ids so rows that page out of a refreshed slice are not replayed
  // later as if they were new live messages.
  const persistedMessageIds = useMemo(() => {
    const ids = new Set<string>();
    for (const message of persistedMessages) {
      ids.add(message.id);
    }
    return ids;
  }, [persistedMessages]);
  useEffect(() => {
    for (const message of persistedMessages) {
      knownPersistedMessageIdsRef.current.add(message.id);
    }
  }, [persistedMessages]);
  const {
    messages: liveMessages,
    sendMessage,
    setMessages,
  } = useChat({
    id: threadId,
    messages: chatMessages,
    transport: new DefaultChatTransport({
      api: "/api/chat",
      credentials: "include",
      headers: (): Record<string, string> => ({
        "X-Athlete-Timezone": browserTimeZone(),
      }),
      prepareSendMessagesRequest: ({
        messages,
      }): { body: Record<string, unknown> } => ({
        body:
          process.env["NEXT_PUBLIC_COACH_CONTEXT_STRATEGY"] === "full_history"
            ? { messages }
            : { message: messages.at(-1) },
      }),
    }),
  });
  const displayedMessages = useMemo<ChatMessage[]>(() => {
    const knownPersistedMessageIds = knownPersistedMessageIdsRef.current;
    const additional = liveMessages
      .filter(
        (m) =>
          !persistedMessageIds.has(m.id) && !knownPersistedMessageIds.has(m.id),
      )
      .map((m) => toLiveChatMessage(m, threadId, userId))
      .filter(
        (m): m is ChatMessage => m !== null && (m.parts ?? []).length > 0,
      );
    return [...persistedMessages, ...additional];
  }, [liveMessages, persistedMessageIds, persistedMessages, threadId, userId]);

  return { displayedMessages, sendMessage, setMessages };
}
