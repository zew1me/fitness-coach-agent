import {
  RequestUsage,
  type AgentInputItem,
  type OpenAIResponsesCompactionArgs,
  type OpenAIResponsesCompactionAwareSession,
  type OpenAIResponsesCompactionResult,
  type SessionHistoryRewriteArgs,
  type SessionHistoryRewriteAwareSession,
} from "@openai/agents";
import * as Sentry from "@sentry/nextjs";
import OpenAI from "openai";

import { modelStateSchema, type ModelState } from "../schemas";

import {
  applyMemoryOperation,
  coachingMemoryRecordSchema,
  type CoachingMemoryOperation,
  type CoachingMemoryRecord,
} from "./coaching-memory";
import { fetchSignalWithTimeout } from "./fetch-signal";

const MODEL_STATE_FETCH_TIMEOUT_MS = 10_000;

type SessionOptions = {
  accessToken: string;
  baseUrl: string;
  extraHeaders?: Record<string, string>;
  fetch?: typeof fetch;
  leaseId: string;
  maxCasRetries?: number;
  signal?: AbortSignal;
};

export type StoredContextEstimate = {
  bytes: number;
  estimatedTokens: number;
  itemCount: number;
  nonUserItemCount: number;
};

export function estimateStoredContext(
  items: AgentInputItem[],
): StoredContextEstimate {
  const bytes = new TextEncoder().encode(JSON.stringify(items)).byteLength;
  return {
    bytes,
    estimatedTokens: Math.ceil(bytes / 4),
    itemCount: items.length,
    nonUserItemCount: items.filter(
      (item) => !("role" in item) || item.role !== "user",
    ).length,
  };
}

function usageDetail(value: unknown, key: string): number {
  if (value === null || typeof value !== "object") return 0;
  const detail = (value as Record<string, unknown>)[key];
  return typeof detail === "number" ? detail : 0;
}

// Rewrite an `input_file` content part into a text description.  OpenAI cannot
// ingest the activity files athletes attach (.fit/.gpx), and `filename`
// alongside a `file_url`/`file_id` reference is rejected outright.  New history
// is sanitized upstream in `toAgentInputItems`; this defends the model-input and
// compaction paths against any `input_file` already persisted before that fix.
function unsupportedFileContentToText(part: { type: string }): {
  type: "input_text";
  text: string;
} {
  const record = part as unknown as Record<string, unknown>;
  const file = record["file"];
  let reference: string | undefined;
  if (typeof file === "string") {
    reference = file;
  } else if (file !== null && typeof file === "object") {
    const fileRecord = file as Record<string, unknown>;
    if (typeof fileRecord["url"] === "string") reference = fileRecord["url"];
    else if (typeof fileRecord["id"] === "string") reference = fileRecord["id"];
  }
  const filename =
    typeof record["filename"] === "string" && record["filename"].length > 0
      ? (record["filename"] as string)
      : "uploaded file";
  return {
    type: "input_text",
    text:
      `Uploaded file: ${filename}` +
      (reference ? `\npublic_url=${reference}` : ""),
  };
}

export class SupabaseAgentSession implements SessionHistoryRewriteAwareSession {
  private readonly options: SessionOptions;
  private state: ModelState | null = null;
  private casRetries = 0;

  constructor(options: SessionOptions) {
    this.options = options;
  }

  private headers(): HeadersInit {
    return {
      Authorization: `Bearer ${this.options.accessToken}`,
      "Content-Type": "application/json",
      ...(this.options.extraHeaders ?? {}),
    };
  }

  private async load(force = false): Promise<ModelState> {
    if (this.state !== null && !force) return this.state;
    const response = await (this.options.fetch ?? fetch)(
      `${this.options.baseUrl}/api/chat/model-state`,
      {
        headers: this.headers(),
        signal: fetchSignalWithTimeout(
          this.options.signal,
          MODEL_STATE_FETCH_TIMEOUT_MS,
        ),
      },
    );
    if (!response.ok)
      throw new Error(`Unable to load model state (${response.status})`);
    this.state = modelStateSchema.parse(await response.json());
    return this.state;
  }

  private async mutate(
    mutation: (
      state: ModelState,
    ) => Pick<ModelState, "items" | "coaching_memory" | "compaction_metadata">,
  ): Promise<void> {
    const attempts = (this.options.maxCasRetries ?? 3) + 1;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      const current = await this.load(attempt > 0);
      const next = mutation(current);
      const response = await (this.options.fetch ?? fetch)(
        `${this.options.baseUrl}/api/chat/model-state`,
        {
          method: "PUT",
          headers: this.headers(),
          signal: fetchSignalWithTimeout(
            this.options.signal,
            MODEL_STATE_FETCH_TIMEOUT_MS,
          ),
          body: JSON.stringify({
            expected_version: current.version,
            lease_id: this.options.leaseId,
            ...next,
          }),
        },
      );
      if (response.ok) {
        this.casRetries = attempt;
        this.state = modelStateSchema.parse(await response.json());
        return;
      }
      if (response.status !== 409 || attempt === attempts - 1) {
        throw new Error(`Unable to replace model state (${response.status})`);
      }
    }
  }

  async getSessionId(): Promise<string> {
    return (await this.load()).thread_id;
  }

  getLastCasRetries(): number {
    return this.casRetries;
  }

  async getItems(limit?: number): Promise<AgentInputItem[]> {
    const items = (await this.load()).items;
    if (limit !== undefined && limit <= 0) return [];
    return limit === undefined ? [...items] : items.slice(-limit);
  }

  async getCoachingMemory(): Promise<CoachingMemoryRecord[]> {
    return (await this.load()).coaching_memory.map((record) =>
      coachingMemoryRecordSchema.parse(record),
    );
  }

  async updateCoachingMemory(
    operation: CoachingMemoryOperation,
  ): Promise<void> {
    await this.mutate((state) => ({
      items: state.items,
      coaching_memory: applyMemoryOperation(
        state.coaching_memory.map((record) =>
          coachingMemoryRecordSchema.parse(record),
        ),
        operation,
      ),
      compaction_metadata: state.compaction_metadata,
    }));
  }

  prepareHistoryItemForModelInput(item: AgentInputItem): AgentInputItem {
    if (
      !("role" in item) ||
      item.role !== "user" ||
      !Array.isArray(item.content)
    )
      return item;
    const content = (item.content as Array<{ type: string }>).flatMap(
      (part) => {
        if (part.type === "input_image") return [];
        if (part.type === "input_file")
          return [unsupportedFileContentToText(part)];
        return [part];
      },
    );
    return { ...item, content } as AgentInputItem;
  }

  async addItems(items: AgentInputItem[]): Promise<void> {
    await this.mutate((state) => ({
      items: [...state.items, ...items],
      coaching_memory: state.coaching_memory,
      compaction_metadata: state.compaction_metadata,
    }));
  }

  async popItem(): Promise<AgentInputItem | undefined> {
    let popped: AgentInputItem | undefined;
    await this.mutate((state) => {
      popped = state.items.at(-1);
      return {
        items: state.items.slice(0, -1),
        coaching_memory: state.coaching_memory,
        compaction_metadata: state.compaction_metadata,
      };
    });
    return popped;
  }

  async clearSession(): Promise<void> {
    await this.replaceAll([], {});
  }

  async replaceAll(
    items: AgentInputItem[],
    metadata: Record<string, unknown>,
  ): Promise<void> {
    await this.mutate((state) => ({
      items,
      coaching_memory: state.coaching_memory,
      compaction_metadata: { ...state.compaction_metadata, ...metadata },
    }));
  }

  async applyHistoryMutations({
    mutations,
  }: SessionHistoryRewriteArgs): Promise<void> {
    await this.mutate((state) => ({
      items: state.items.map((item) => {
        if (!("type" in item) || item.type !== "function_call") return item;
        const replacement = mutations.find(
          (entry) => entry.callId === item.callId,
        );
        return replacement?.replacement ?? item;
      }),
      coaching_memory: state.coaching_memory,
      compaction_metadata: state.compaction_metadata,
    }));
  }
}

type CompactionSessionOptions = {
  underlyingSession: SessionHistoryRewriteAwareSession & {
    replaceAll(
      items: AgentInputItem[],
      metadata: Record<string, unknown>,
    ): Promise<void>;
    getLastCasRetries?: () => number;
  };
  client?: OpenAI;
  model?: string;
  autoCompactTokens?: number;
  autoCompactNonUserItems?: number;
};

export class DurableCompactionSession implements OpenAIResponsesCompactionAwareSession {
  private readonly client: OpenAI;
  private readonly options: CompactionSessionOptions;

  constructor(options: CompactionSessionOptions) {
    this.options = options;
    this.client = options.client ?? new OpenAI();
  }

  getSessionId = (): Promise<string> =>
    this.options.underlyingSession.getSessionId();
  getItems = (limit?: number): Promise<AgentInputItem[]> =>
    this.options.underlyingSession.getItems(limit);
  addItems = (items: AgentInputItem[]): Promise<void> =>
    this.options.underlyingSession.addItems(items);
  popItem = (): Promise<AgentInputItem | undefined> =>
    this.options.underlyingSession.popItem();
  clearSession = (): Promise<void> =>
    this.options.underlyingSession.clearSession();
  prepareHistoryItemForModelInput = (item: AgentInputItem): AgentInputItem =>
    this.options.underlyingSession.prepareHistoryItemForModelInput?.(item) ??
    item;

  // Trigger selection, remote compaction, atomic replacement, and metrics are one operation.
  // eslint-disable-next-line complexity
  async runCompaction(
    args: OpenAIResponsesCompactionArgs = {},
  ): Promise<OpenAIResponsesCompactionResult | null> {
    const startedAt = performance.now();
    const items = await this.getItems();
    const before = estimateStoredContext(items);
    const shouldCompact =
      args.force === true ||
      before.estimatedTokens >= (this.options.autoCompactTokens ?? 120_000) ||
      before.nonUserItemCount >= (this.options.autoCompactNonUserItems ?? 40);
    if (!shouldCompact || items.length === 0) return null;

    const compactArgs: Partial<OpenAIResponsesCompactionArgs> = { ...args };
    delete compactArgs.force;
    const compacted = await this.client.responses.compact({
      ...compactArgs,
      model: this.options.model ?? "gpt-5.4-mini",
      // Strip input_image parts (and any other model-incompatible content)
      // before compacting, matching the sanitization applied elsewhere via
      // prepareHistoryItemForModelInput.
      input: items.map((item) =>
        this.prepareHistoryItemForModelInput(item),
      ) as OpenAI.Responses.ResponseInput,
    });
    const output = compacted.output as AgentInputItem[];
    if (!Array.isArray(output) || output.length === 0) {
      throw new Error(
        `Compaction returned ${Array.isArray(output) ? 0 : typeof output} items; refusing to wipe durable context`,
      );
    }
    const after = estimateStoredContext(output);
    await this.options.underlyingSession.replaceAll(output, {
      trigger: args.force === true ? "forced" : "auto",
      compacted_at: new Date().toISOString(),
      before_bytes: before.bytes,
      before_tokens: before.estimatedTokens,
      before_items: before.itemCount,
      after_bytes: after.bytes,
      after_tokens: after.estimatedTokens,
      after_items: after.itemCount,
    });
    Sentry.logger.info("coach compaction complete", {
      trigger: args.force === true ? "forced" : "auto",
      before_bytes: before.bytes,
      before_tokens: before.estimatedTokens,
      before_items: before.itemCount,
      after_bytes: after.bytes,
      after_tokens: after.estimatedTokens,
      after_items: after.itemCount,
      latency_ms: Math.round(performance.now() - startedAt),
      cas_retries: this.options.underlyingSession.getLastCasRetries?.() ?? 0,
      request_count: 1,
      input_tokens: compacted.usage.input_tokens,
      cached_tokens: usageDetail(
        compacted.usage.input_tokens_details,
        "cached_tokens",
      ),
      output_tokens: compacted.usage.output_tokens,
      reasoning_tokens: usageDetail(
        compacted.usage.output_tokens_details,
        "reasoning_tokens",
      ),
      total_tokens: compacted.usage.total_tokens,
      max_request_input: compacted.usage.input_tokens,
    });
    return {
      usage: new RequestUsage({
        ...compacted.usage,
        endpoint: "responses.compact",
      }),
    };
  }
}
