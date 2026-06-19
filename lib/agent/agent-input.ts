import type { AgentInputItem } from "@openai/agents";
import type { UIMessage } from "ai";

function partRecord(part: UIMessage["parts"][number]): Record<string, unknown> {
  return part as unknown as Record<string, unknown>;
}

type UserContent = Exclude<
  Extract<AgentInputItem, { role: "user" }>["content"],
  string
>;

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
  if (value["mediaType"].startsWith("image/")) {
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
  const items: AgentInputItem[] = [
    {
      type: "function_call",
      callId,
      name,
      arguments: JSON.stringify(value["input"] ?? {}),
      status: "completed",
    },
  ];
  if (value["state"] === "output-available") {
    items.push({
      type: "function_call_result",
      callId,
      name,
      output: JSON.stringify(value["output"] ?? null),
      status: "completed",
    });
  }
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
  return messages.flatMap((message): AgentInputItem[] => {
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
  });
}
