// Pure leaf module converting between Agents SDK item shapes and raw OpenAI
// Responses API shapes. Used both on every model turn (model-input replay)
// and during compaction — see docs/COMPACTION_DESIGN.md.
import type { AgentInputItem } from "@openai/agents";

function extractFileRef(record: Record<string, unknown>): {
  publicUrl?: string;
  fileId?: string;
} {
  const file = record["file"];
  if (typeof file === "string") return { publicUrl: file };
  if (file !== null && typeof file === "object") {
    const f = file as Record<string, unknown>;
    const out: { publicUrl?: string; fileId?: string } = {};
    if (typeof f["url"] === "string") out.publicUrl = f["url"];
    if (typeof f["id"] === "string") out.fileId = f["id"];
    return out;
  }
  return {};
}

// Rewrite an `input_file` content part into a text description.  OpenAI cannot
// ingest the activity files athletes attach (.fit/.gpx), and `filename`
// alongside a `file_url`/`file_id` reference is rejected outright.  New history
// is sanitized upstream in `toAgentInputItems`; this defends the model-input and
// compaction paths against any `input_file` already persisted before that fix.
export function unsupportedFileContentToText(part: { type: string }): {
  type: "input_text";
  text: string;
} {
  const record = part as unknown as Record<string, unknown>;
  const { publicUrl, fileId } = extractFileRef(record);
  const filename =
    typeof record["filename"] === "string" && record["filename"].length > 0
      ? (record["filename"] as string)
      : "uploaded file";
  return {
    type: "input_text",
    text:
      `Uploaded file: ${filename}` +
      (publicUrl ? `\npublic_url=${publicUrl}` : "") +
      (fileId ? `\nfile_id=${fileId}` : ""),
  };
}

function withCallIdField(
  record: Record<string, unknown>,
  field: "callId" | "call_id",
): AgentInputItem {
  const callId = record["callId"] ?? record["call_id"];
  if (typeof callId !== "string") {
    return record as unknown as AgentInputItem;
  }
  const rest = { ...record };
  delete rest[field === "callId" ? "call_id" : "callId"];
  return { ...rest, [field]: callId } as unknown as AgentInputItem;
}

// The Agents SDK's `reasoning` item stores its visible summary under
// `content` (input_text parts). The raw Responses API `reasoning` input item
// has a different, required shape: `summary` (summary_text parts) — the same
// field name means something else on each side. Compacting without this
// conversion 400s with "Missing required parameter: 'input[N].summary'".
// The API also rejects a non-empty `content` (chain-of-thought text) on
// replay unless the response that produced it enabled
// `reasoning.encrypted_content`, which this app never requests, so
// `rawContent` is dropped rather than forwarded as `content`.
function toResponsesCompactReasoningItem(
  record: Record<string, unknown>,
): AgentInputItem {
  const content = Array.isArray(record["content"]) ? record["content"] : [];
  const summary = (content as Array<Record<string, unknown>>)
    .filter((part) => typeof part["text"] === "string")
    .map((part) => ({ type: "summary_text", text: part["text"] }));
  const compacted: Record<string, unknown> = {
    type: "reasoning",
    id: record["id"],
    summary,
  };
  if (typeof record["status"] === "string") {
    compacted["status"] = record["status"];
  }
  return compacted as unknown as AgentInputItem;
}

// The Agents SDK's function_call_result.output content parts use tool-output
// type literals ("text"/"image"/"file"); the raw Responses API's
// function_call_output.output array uses the input content type literals
// ("input_text"/"input_image"/"input_file") instead — sending the SDK's
// literal 400s with "Invalid value: 'text'".
const OUTPUT_CONTENT_TYPE_MAP: Record<string, string> = {
  text: "input_text",
  image: "input_image",
  file: "input_file",
};

function toResponsesCompactOutputContentPart(value: unknown): unknown {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return value;
  }
  const record = value as Record<string, unknown>;
  const mapped = OUTPUT_CONTENT_TYPE_MAP[record["type"] as string];
  return mapped === undefined ? record : { ...record, type: mapped };
}

function toResponsesCompactFunctionCallResultItem(
  record: Record<string, unknown>,
): AgentInputItem {
  const callId = record["callId"] ?? record["call_id"];
  if (typeof callId !== "string") {
    return record as unknown as AgentInputItem;
  }
  // The Responses API only accepts a string or an array for
  // function_call_output.output; the Agents SDK also allows a single
  // content-part object (e.g. `{ type: "text", text }`), which 400s unless
  // wrapped in an array.
  const rawOutput = record["output"];
  const output =
    typeof rawOutput === "string"
      ? rawOutput
      : Array.isArray(rawOutput)
        ? rawOutput.map(toResponsesCompactOutputContentPart)
        : [toResponsesCompactOutputContentPart(rawOutput)];
  const compacted: Record<string, unknown> = {
    type: "function_call_output",
    call_id: callId,
    output,
  };
  if (typeof record["status"] === "string") {
    compacted["status"] = record["status"];
  }
  if (typeof record["id"] === "string") {
    compacted["id"] = record["id"];
  }
  return compacted as unknown as AgentInputItem;
}

export function toResponsesCompactInputItem(
  record: Record<string, unknown>,
): AgentInputItem {
  const type = record["type"];
  if (type === "function_call_result") {
    return toResponsesCompactFunctionCallResultItem(record);
  }
  if (type === "reasoning" && typeof record["id"] === "string") {
    return toResponsesCompactReasoningItem(record);
  }
  return withCallIdField(record, "call_id");
}

const PROVIDER_METADATA_KEYS = new Set(["providerData", "providerMetadata"]);

// Every Agents SDK item (and every nested content part) may carry a
// `providerData`/`providerMetadata` bag for SDK-internal bookkeeping, which
// the raw Responses API `compact` endpoint doesn't recognize and rejects.
// Strip those keys recursively rather than allowlisting known-good fields,
// so item types the SDK hasn't been taught about yet (e.g. `namespace` on
// function calls, `rawContent` on reasoning items) survive untouched.
function stripProviderMetadata(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(stripProviderMetadata);
  }
  if (value !== null && typeof value === "object") {
    const sanitized: Record<string, unknown> = {};
    for (const [key, entry] of Object.entries(value)) {
      if (PROVIDER_METADATA_KEYS.has(key)) continue;
      sanitized[key] = stripProviderMetadata(entry);
    }
    return sanitized;
  }
  return value;
}

export function sanitizeResponsesCompactInputItem(
  item: AgentInputItem,
): AgentInputItem {
  return stripProviderMetadata(item) as AgentInputItem;
}

function omittedToolOutputMessage(): AgentInputItem {
  return {
    role: "assistant",
    status: "completed",
    content: [
      {
        type: "output_text",
        text:
          "Historical tool output omitted from model replay. " +
          "The visible chat transcript is preserved separately.",
      },
    ],
  } as AgentInputItem;
}

export function prepareFunctionItemForModelInput(
  item: AgentInputItem,
): AgentInputItem {
  if (!("type" in item)) return item;
  const record = item as unknown as Record<string, unknown>;
  const itemType = record["type"];
  if (itemType === "function_call" || itemType === "function_call_result") {
    return withCallIdField(record, "callId");
  }
  if (itemType === "function_call_output") {
    return omittedToolOutputMessage();
  }
  return item;
}
