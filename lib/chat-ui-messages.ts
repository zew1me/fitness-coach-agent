import type { UIMessage } from "ai";

import type { ChatMessage } from "./types";

export function onlyWelcomeMessage(messages: ChatMessage[]): boolean {
  const firstMessage = messages[0];
  return (
    messages.length === 1 &&
    firstMessage !== undefined &&
    firstMessage.role === "assistant" &&
    firstMessage.metadata.message_kind === "welcome"
  );
}

export function toUiMessage(message: ChatMessage): UIMessage {
  return {
    id: message.id,
    parts: message.parts as UIMessage["parts"],
    role: message.role,
  };
}

export function toLiveChatMessage(
  message: UIMessage,
  threadId: string,
  userId: string,
): ChatMessage | null {
  if (message.role !== "assistant" && message.role !== "user") {
    return null;
  }

  return {
    attachments: [],
    parts: message.parts as ChatMessage["parts"],
    created_at: new Date().toISOString(),
    id: message.id,
    metadata: { message_kind: "streaming" },
    role: message.role,
    thread_id: threadId,
    user_id: userId,
  };
}
