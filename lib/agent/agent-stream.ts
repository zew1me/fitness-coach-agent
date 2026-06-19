import type { RunStreamEvent } from "@openai/agents";
import * as Sentry from "@sentry/nextjs";
import type { UIMessageStreamWriter } from "ai";

type StreamState = {
  textId: string;
  textStarted: boolean;
};

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

// Non-string values are already parsed JS objects — pass through as-is.
// Strings are JSON-decoded; unparseable strings fall back to {}.
function parseToolInput(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return {};
  }
}

function callIdentity(rawItem: Record<string, unknown>): {
  callId: string;
  name: string;
} | null {
  const callId = rawItem["callId"] ?? rawItem["id"];
  const name = rawItem["name"];
  return typeof callId === "string" && typeof name === "string"
    ? { callId, name }
    : null;
}

function warnUnhandledTextLikeType(data: Record<string, unknown> | null): void {
  const t = data?.["type"];
  if (typeof t === "string" && (t.includes("text") || t.includes("delta"))) {
    Sentry.logger.warn("coach-stream: unhandled text-like event type", {
      type: t,
    });
  }
}

function handleModelDelta(
  event: Extract<RunStreamEvent, { type: "raw_model_stream_event" }>,
  writer: UIMessageStreamWriter,
  state: StreamState,
): void {
  const data = record(event.data);
  if (data?.["type"] !== "output_text_delta") {
    warnUnhandledTextLikeType(data);
    return;
  }
  const delta = data["delta"];
  if (typeof delta !== "string" || delta.length === 0) return;
  if (!state.textStarted) {
    Sentry.logger.info("coach-stream: text streaming started", {
      id: state.textId,
    });
    writer.write({ type: "text-start", id: state.textId });
    state.textStarted = true;
  }
  writer.write({ type: "text-delta", id: state.textId, delta });
}

function handleRunItem(
  event: Extract<RunStreamEvent, { type: "run_item_stream_event" }>,
  writer: UIMessageStreamWriter,
): void {
  const rawItem = record(event.item.rawItem);
  if (rawItem === null) return;
  const identity = callIdentity(rawItem);
  if (identity === null) return;

  if (event.name === "tool_called") {
    writer.write({
      type: "tool-input-available",
      toolCallId: identity.callId,
      toolName: identity.name,
      input: parseToolInput(rawItem["arguments"]),
    });
  } else if (event.name === "tool_output") {
    writer.write({
      type: "tool-output-available",
      toolCallId: identity.callId,
      output: record(event.item)?.["output"] ?? null,
    });
  }
}

export function writeAgentStreamEvent(
  event: RunStreamEvent,
  writer: UIMessageStreamWriter,
  state: StreamState,
): void {
  if (event.type === "raw_model_stream_event") {
    handleModelDelta(event, writer, state);
  } else if (event.type === "run_item_stream_event") {
    handleRunItem(event, writer);
  }
}

export function finishAgentText(
  writer: UIMessageStreamWriter,
  state: StreamState,
): void {
  if (!state.textStarted) return;
  writer.write({ type: "text-end", id: state.textId });
}
