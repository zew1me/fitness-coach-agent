import type { AgentInputItem } from "@openai/agents";
import type { UIMessage } from "ai";

import { convertUnsupportedFilePartsToText } from "./message-context";

// Safe: every UIMessage part is a plain object with a discriminant `type` field.
function partRecord(part: UIMessage["parts"][number]): Record<string, unknown> {
  return part as unknown as Record<string, unknown>;
}

type UserContent = Exclude<
  Extract<AgentInputItem, { role: "user" }>["content"],
  string
>;

const SUPPORTED_IMAGE_TYPES = new Set([
  "image/gif",
  "image/jpeg",
  "image/png",
  "image/webp",
]);

function fileContentItem(
  value: Record<string, unknown>,
): UserContent[number] | null {
  if (
    value["type"] !== "file" ||
    typeof value["url"] !== "string" ||
    typeof value["mediaType"] !== "string"
  ) {
    return null;
  }
  if (SUPPORTED_IMAGE_TYPES.has(value["mediaType"])) {
    return { type: "input_image", image: value["url"] };
  }
  return {
    type: "input_file",
    file: { url: value["url"] },
    ...(typeof value["filename"] === "string"
      ? { filename: value["filename"] }
      : {}),
  };
}

function userContent(message: UIMessage): UserContent {
  const content: UserContent = [];
  for (const part of message.parts) {
    if (part.type === "text") {
      if (part.text.length > 0)
        content.push({ type: "input_text", text: part.text });
      continue;
    }
    const item = fileContentItem(partRecord(part));
    if (item !== null) content.push(item);
  }
  return content;
}

function toolResultItem(
  callId: string,
  name: string,
  state: unknown,
  value: Record<string, unknown>,
): AgentInputItem | null {
  if (state === "output-available") {
    return {
      type: "function_call_result",
      callId,
      name,
      output: JSON.stringify(value["output"] ?? null),
      status: "completed",
    };
  }
  if (state === "output-error") {
    return {
      type: "function_call_result",
      callId,
      name,
      output: JSON.stringify({
        error: value["errorText"] ?? "Tool call failed",
      }),
      status: "completed",
    };
  }
  return null;
}

function toolPartItems(value: Record<string, unknown>): AgentInputItem[] {
  const type = value["type"];
  const callId = value["toolCallId"];
  if (
    typeof type !== "string" ||
    !type.startsWith("tool-") ||
    typeof callId !== "string"
  ) {
    return [];
  }
  const name = type.slice("tool-".length);
  const state = value["state"];
  const isComplete = state === "output-available" || state === "output-error";
  const items: AgentInputItem[] = [
    {
      type: "function_call",
      callId,
      name,
      arguments: JSON.stringify(value["input"] ?? {}),
      status: isComplete ? "completed" : "in_progress",
    },
  ];
  const result = toolResultItem(callId, name, state, value);
  if (result !== null) items.push(result);
  return items;
}

function assistantItems(message: UIMessage): AgentInputItem[] {
  const text = message.parts
    .map((part) => (part.type === "text" ? part.text : ""))
    .filter(Boolean)
    .join("\n");

  const items: AgentInputItem[] = message.parts.flatMap((part) =>
    toolPartItems(partRecord(part)),
  );

  if (text.length > 0) {
    items.push({
      role: "assistant",
      status: "completed",
      content: [{ type: "output_text", text }],
    });
  }
  return items;
}

export function toAgentInputItems(messages: UIMessage[]): AgentInputItem[] {
  // Replace unsupported (non-image) file parts — e.g. .fit/.gpx activity files —
  // with text descriptions before building model items.  The live route already
  // does this, but the durable-session bootstrap and delegation planner reach
  // this chokepoint with raw transcript messages.  Idempotent: a second pass
  // finds no remaining non-image file parts.
  return convertUnsupportedFilePartsToText(messages).flatMap(
    (message): AgentInputItem[] => {
      if (message.role === "assistant") return assistantItems(message);

      if (message.role === "system") {
        const content = message.parts
          .map((part) => (part.type === "text" ? part.text : ""))
          .filter(Boolean)
          .join("\n");
        if (content.length === 0) return [];
        return [
          {
            role: "system",
            content,
          },
        ];
      }

      const content = userContent(message);
      if (content.length === 0) return [];
      return [{ role: "user", content }];
    },
  );
}
