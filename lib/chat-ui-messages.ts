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

function readString(
  record: Record<string, unknown>,
  key: string,
): string | null {
  const value = record[key];
  return typeof value === "string" ? value : null;
}

function legacyAttachmentToFilePart(
  attachment: Record<string, unknown>,
): UIMessage["parts"][number] | null {
  const url = readString(attachment, "public_url");
  if (url === null) return null;
  const mediaType =
    readString(attachment, "content_type") ??
    readString(attachment, "mediaType") ??
    "application/octet-stream";
  const filename = readString(attachment, "filename") ?? "attachment";
  return { type: "file", mediaType, filename, url };
}

// Real persisted messages always have `parts` populated (the chat-parts migration
// backfilled every legacy row). This shim covers in-flight test fixtures and
// any callers still constructing the legacy shape: synthesize parts from the
// text `content` + legacy `attachments` join so the renderer never has to
// branch on shape.
export function deriveParts(message: ChatMessage): UIMessage["parts"] {
  if (message.parts && message.parts.length > 0) {
    return message.parts as UIMessage["parts"];
  }
  const synthesized: UIMessage["parts"] = [];
  if (message.content && message.content.length > 0) {
    synthesized.push({ type: "text", text: message.content });
  }
  for (const attachment of message.attachments) {
    const part = legacyAttachmentToFilePart(attachment);
    if (part !== null) synthesized.push(part);
  }
  return synthesized;
}

export function toUiMessage(message: ChatMessage): UIMessage {
  return {
    id: message.id,
    parts: deriveParts(message),
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
    parts: message.parts as NonNullable<ChatMessage["parts"]>,
    created_at: new Date().toISOString(),
    id: message.id,
    metadata: { message_kind: "streaming" },
    role: message.role,
    thread_id: threadId,
    user_id: userId,
  };
}
