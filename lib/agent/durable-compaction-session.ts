import {
  RequestUsage,
  type AgentInputItem,
  type OpenAIResponsesCompactionArgs,
  type OpenAIResponsesCompactionAwareSession,
  type OpenAIResponsesCompactionResult,
  type SessionHistoryRewriteAwareSession,
} from "@openai/agents";
import * as Sentry from "@sentry/nextjs";
import OpenAI from "openai";

import {
  sanitizeResponsesCompactInputItem,
  toResponsesCompactInputItem,
} from "./responses-item-shapes";

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

type OpenAICompactOptions = Partial<
  Pick<
    OpenAI.Responses.ResponseCompactParams,
    | "instructions"
    | "previous_response_id"
    | "prompt_cache_key"
    | "prompt_cache_retention"
    | "service_tier"
  >
>;

type AgentsCompactionArgs = OpenAIResponsesCompactionArgs &
  OpenAICompactOptions & {
    responseId?: string | null;
  };

function toOpenAICompactOptions(
  args: OpenAIResponsesCompactionArgs,
): OpenAICompactOptions {
  const source = args as AgentsCompactionArgs;
  const options: OpenAICompactOptions = {};
  const previousResponseId =
    source.responseId ?? source.previous_response_id ?? undefined;
  if (previousResponseId !== undefined)
    options.previous_response_id = previousResponseId;
  if (source.instructions !== undefined)
    options.instructions = source.instructions;
  if (source.prompt_cache_key !== undefined)
    options.prompt_cache_key = source.prompt_cache_key;
  if (source.prompt_cache_retention !== undefined)
    options.prompt_cache_retention = source.prompt_cache_retention;
  if (source.service_tier !== undefined)
    options.service_tier = source.service_tier;
  return options;
}

type CompactionTrigger = "forced" | "auto";

type CompactionThresholds = Pick<
  CompactionSessionOptions,
  "autoCompactTokens" | "autoCompactNonUserItems"
>;

function shouldTriggerCompaction(
  args: OpenAIResponsesCompactionArgs,
  before: StoredContextEstimate,
  thresholds: CompactionThresholds,
): boolean {
  return (
    args.force === true ||
    before.estimatedTokens >= (thresholds.autoCompactTokens ?? 120000) ||
    before.nonUserItemCount >= (thresholds.autoCompactNonUserItems ?? 40)
  );
}

// Strip input_image parts (and any other model-incompatible content) before
// compacting, matching the sanitization applied elsewhere via
// prepareHistoryItemForModelInput. Convert SDK `callId` to the Responses API
// `call_id` field so compact accepts function calls.
function buildCompactionInput(
  items: AgentInputItem[],
  prepareItem: (item: AgentInputItem) => AgentInputItem,
): OpenAI.Responses.ResponseInput {
  return items.map((item) => {
    const prepared = prepareItem(item);
    const compactInput = toResponsesCompactInputItem(prepared);
    return sanitizeResponsesCompactInputItem(compactInput);
  }) as OpenAI.Responses.ResponseInput;
}

function assertValidCompactionOutput(output: unknown): AgentInputItem[] {
  if (!Array.isArray(output) || output.length === 0) {
    throw new Error(
      `Compaction returned ${Array.isArray(output) ? 0 : typeof output} items; refusing to wipe durable context`,
    );
  }
  return output as AgentInputItem[];
}

function buildCompactionMetadata(
  trigger: CompactionTrigger,
  before: StoredContextEstimate,
  after: StoredContextEstimate,
): Record<string, unknown> {
  return {
    trigger,
    compacted_at: new Date().toISOString(),
    before_bytes: before.bytes,
    before_tokens: before.estimatedTokens,
    before_items: before.itemCount,
    after_bytes: after.bytes,
    after_tokens: after.estimatedTokens,
    after_items: after.itemCount,
  };
}

type CompactResponse = Awaited<ReturnType<OpenAI["responses"]["compact"]>>;

function logCompactionTelemetry(params: {
  trigger: CompactionTrigger;
  before: StoredContextEstimate;
  after: StoredContextEstimate;
  latencyMs: number;
  casRetries: number;
  compacted: CompactResponse;
}): void {
  const { trigger, before, after, latencyMs, casRetries, compacted } = params;
  Sentry.logger.info("coach compaction complete", {
    trigger,
    before_bytes: before.bytes,
    before_tokens: before.estimatedTokens,
    before_items: before.itemCount,
    after_bytes: after.bytes,
    after_tokens: after.estimatedTokens,
    after_items: after.itemCount,
    latency_ms: latencyMs,
    cas_retries: casRetries,
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
}

function toRequestUsage(compacted: CompactResponse): RequestUsage {
  return new RequestUsage({
    ...compacted.usage,
    endpoint: "responses.compact",
  });
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

  async runCompaction(
    args: OpenAIResponsesCompactionArgs = {},
  ): Promise<OpenAIResponsesCompactionResult | null> {
    const startedAt = performance.now();
    const items = await this.getItems();
    const before = estimateStoredContext(items);

    if (
      !shouldTriggerCompaction(args, before, this.options) ||
      items.length === 0
    ) {
      return null;
    }

    const trigger: CompactionTrigger = args.force === true ? "forced" : "auto";
    const compacted = await this.client.responses.compact({
      ...toOpenAICompactOptions(args),
      model: this.options.model ?? "gpt-5.4-mini",
      input: buildCompactionInput(items, (item) =>
        this.prepareHistoryItemForModelInput(item),
      ),
    });
    const output = assertValidCompactionOutput(compacted.output);
    const after = estimateStoredContext(output);

    await this.options.underlyingSession.replaceAll(
      output,
      buildCompactionMetadata(trigger, before, after),
    );

    logCompactionTelemetry({
      trigger,
      before,
      after,
      latencyMs: Math.round(performance.now() - startedAt),
      casRetries: this.options.underlyingSession.getLastCasRetries?.() ?? 0,
      compacted,
    });

    return { usage: toRequestUsage(compacted) };
  }
}
