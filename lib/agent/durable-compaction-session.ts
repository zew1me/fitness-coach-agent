// Wraps a SupabaseAgentSession-shaped session to add OpenAI
// responses.compact-based compaction. See docs/COMPACTION_DESIGN.md for the
// full design.
import {
  RequestUsage,
  type AgentInputItem,
  type OpenAIResponsesCompactionArgs,
  type OpenAIResponsesCompactionAwareSession,
  type OpenAIResponsesCompactionResult,
  type SessionHistoryRewriteAwareSession,
} from "@openai/agents";
import OpenAI from "openai";

import { defaultLogger, type Logger } from "../logger";

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

function serializedByteLength(value: unknown): number {
  return new TextEncoder().encode(JSON.stringify(value)).byteLength;
}

// Token estimate for any JSON-serializable payload. The compaction request input is
// a Responses `ResponseInput`, not `AgentInputItem[]`, and only its token size is
// wanted — `itemCount`/`nonUserItemCount` do not carry the same meaning once items
// are in wire shape, since the `role` check misreads them. Estimating it directly
// avoids casting a `ResponseInput` through `estimateStoredContext` to reach one field.
function estimateSerializedTokens(value: unknown): number {
  return Math.ceil(serializedByteLength(value) / 4);
}

function isUserMessage(item: AgentInputItem): boolean {
  return "role" in item && item.role === "user";
}

function isCompactionItem(item: AgentInputItem): boolean {
  return "type" in item && item.type === "compaction";
}

export function estimateStoredContext(
  items: AgentInputItem[],
): StoredContextEstimate {
  const bytes = serializedByteLength(items);
  return {
    bytes,
    estimatedTokens: Math.ceil(bytes / 4),
    itemCount: items.length,
    nonUserItemCount: items.filter((item) => !isUserMessage(item)).length,
  };
}

export type FoldableContextEstimate = Pick<
  StoredContextEstimate,
  "bytes" | "estimatedTokens" | "itemCount"
>;

// The part of stored history that compaction can actually shrink.
// `responses.compact` returns "a list of all user messages, followed by a
// single compaction item" (openai `CompactedResponse.output`), so every user
// message survives verbatim and an earlier compaction item is only re-wrapped.
// Only the remaining assistant, tool call/result, and reasoning items fold.
export function estimateFoldableContext(
  items: AgentInputItem[],
): FoldableContextEstimate {
  const foldable = items.filter(
    (item) => !isUserMessage(item) && !isCompactionItem(item),
  );
  const bytes = serializedByteLength(foldable);
  return {
    bytes,
    estimatedTokens: Math.ceil(bytes / 4),
    itemCount: foldable.length,
  };
}

function usageDetail(value: unknown, key: string): number {
  if (value === null || typeof value !== "object") return 0;
  const detail = (value as Record<string, unknown>)[key];
  return typeof detail === "number" ? detail : 0;
}

export type TokenEstimateComparison = {
  estimateMinusActualTokens: number;
  estimateErrorPercent: number | null;
  estimatedToActualRatio: number | null;
};

function roundCalibrationMetric(value: number): number {
  return Math.round(value * 10_000) / 10_000;
}

export function compareTokenEstimate(
  estimatedTokens: number,
  actualTokens: number,
): TokenEstimateComparison {
  const estimateMinusActualTokens = estimatedTokens - actualTokens;
  if (actualTokens <= 0) {
    return {
      estimateMinusActualTokens,
      estimateErrorPercent: null,
      estimatedToActualRatio: null,
    };
  }
  return {
    estimateMinusActualTokens,
    estimateErrorPercent: roundCalibrationMetric(
      (estimateMinusActualTokens / actualTokens) * 100,
    ),
    estimatedToActualRatio: roundCalibrationMetric(
      estimatedTokens / actualTokens,
    ),
  };
}

type OpenAICompactOptions = Partial<
  Pick<
    OpenAI.Responses.ResponseCompactParams,
    | "instructions"
    | "prompt_cache_key"
    | "prompt_cache_retention"
    | "service_tier"
  >
>;

type AgentsCompactionArgs = OpenAIResponsesCompactionArgs &
  OpenAICompactOptions;

// Deliberately never forwards responseId/previous_response_id: this session's
// `input` (built from buildCompactionInput) is always the complete, Supabase-backed
// authoritative history. `previous_response_id` tells responses.compact to layer
// `input` on top of the server-remembered conversation for that response id —
// since that conversation is the same one already present in `input`, the two
// combine into a duplicated (and duplicate-item-id) history. The SDK's
// post-turn auto-compaction call always supplies a responseId
// (`runCompactionOnSession` in @openai/agents-core), so every SDK-initiated
// compaction would hit this — see docs/COMPACTION_DESIGN.md.
function toOpenAICompactOptions(
  args: OpenAIResponsesCompactionArgs,
): OpenAICompactOptions {
  const source = args as AgentsCompactionArgs;
  const options: OpenAICompactOptions = {};
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

// "forced" labels an explicit `force` and the orchestrator's pre-turn size
// check (its historical label); everything else is "auto".
type CompactionTrigger = "forced" | "auto";

type CompactionSkipReason = "nothing_to_fold" | "below_min_gain";

type CompactionThresholds = Pick<
  CompactionSessionOptions,
  "autoCompactTokens" | "autoCompactNonUserItems"
>;

// A coach turn can replay durable history through delegation, the lead, and a
// post-tool follow-up inside one TPM window. Keeping one request below the
// model context window is therefore insufficient; compact early enough to
// leave room for the turn's other model calls under the 200k TPM quota.
export const DEFAULT_AUTO_COMPACT_TOKENS = 60_000;
const DEFAULT_AUTO_COMPACT_NON_USER_ITEMS = 40;
// Size-triggered compaction waits until it can fold at least this share of
// the token threshold (15k by default). Retained user messages alone can sit
// above the threshold, and each compaction re-sends the whole window, so
// compacting a few thousand foldable tokens every turn costs far more TPM
// and latency than carrying them for a few turns.
const MIN_FOLDABLE_SHARE_OF_THRESHOLD = 0.25;

type CompactionRequest = {
  // Explicit callers: compact regardless of thresholds or expected gain.
  force: boolean;
  // Pre-turn size of the next model request (stored history plus the
  // incoming turn). Absent, the threshold applies to stored history.
  projected?: StoredContextEstimate;
};

type CompactionPlan =
  | { action: "none" }
  | {
      action: "skip";
      trigger: CompactionTrigger;
      reason: CompactionSkipReason;
    }
  | { action: "compact"; trigger: CompactionTrigger };

function resolveThresholds(thresholds: CompactionThresholds): {
  tokens: number;
  nonUserItems: number;
} {
  return {
    tokens: thresholds.autoCompactTokens ?? DEFAULT_AUTO_COMPACT_TOKENS,
    nonUserItems:
      thresholds.autoCompactNonUserItems ?? DEFAULT_AUTO_COMPACT_NON_USER_ITEMS,
  };
}

// The non-user-item trigger already implies foldable items and bounds how
// many accumulate, so only a size trigger has to clear the minimum gain.
function skipReason(
  foldable: FoldableContextEstimate,
  sizeTriggerOnly: boolean,
  tokenThreshold: number,
): CompactionSkipReason | null {
  if (foldable.itemCount === 0) return "nothing_to_fold";
  const minFoldableTokens = Math.ceil(
    tokenThreshold * MIN_FOLDABLE_SHARE_OF_THRESHOLD,
  );
  if (sizeTriggerOnly && foldable.estimatedTokens < minFoldableTokens) {
    return "below_min_gain";
  }
  return null;
}

// Compact only when compaction can make progress, as pi does: its
// `prepareCompaction` returns nothing to compact when there is nothing to
// summarize. A size trigger that only folds a few tokens would otherwise
// re-run on every turn once retained user messages exceed the threshold.
function planCompaction(
  request: CompactionRequest,
  before: StoredContextEstimate,
  foldable: FoldableContextEstimate,
  thresholds: CompactionThresholds,
): CompactionPlan {
  if (request.force) return { action: "compact", trigger: "forced" };

  const limits = resolveThresholds(thresholds);
  const itemTriggered = before.nonUserItemCount >= limits.nonUserItems;
  const sizeTriggered =
    (request.projected ?? before).estimatedTokens >= limits.tokens;
  if (!itemTriggered && !sizeTriggered) return { action: "none" };

  const trigger: CompactionTrigger =
    sizeTriggered && request.projected !== undefined ? "forced" : "auto";
  const reason = skipReason(foldable, !itemTriggered, limits.tokens);
  return reason === null
    ? { action: "compact", trigger }
    : { action: "skip", trigger, reason };
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
  logger: Logger;
  trigger: CompactionTrigger;
  before: StoredContextEstimate;
  preparedInputTokens: number;
  after: StoredContextEstimate;
  latencyMs: number;
  casRetries: number;
  compacted: CompactResponse;
}): void {
  const {
    logger,
    trigger,
    before,
    preparedInputTokens,
    after,
    latencyMs,
    casRetries,
    compacted,
  } = params;
  const storedTokenComparison = compareTokenEstimate(
    before.estimatedTokens,
    compacted.usage.input_tokens,
  );
  const preparedTokenComparison = compareTokenEstimate(
    preparedInputTokens,
    compacted.usage.input_tokens,
  );
  logger.info("coach compaction complete", {
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
    stored_estimated_input_tokens: before.estimatedTokens,
    stored_estimate_minus_actual_tokens:
      storedTokenComparison.estimateMinusActualTokens,
    stored_estimate_error_percent: storedTokenComparison.estimateErrorPercent,
    stored_estimated_to_actual_ratio:
      storedTokenComparison.estimatedToActualRatio,
    prepared_estimated_input_tokens: preparedInputTokens,
    prepared_estimate_minus_actual_tokens:
      preparedTokenComparison.estimateMinusActualTokens,
    prepared_estimate_error_percent:
      preparedTokenComparison.estimateErrorPercent,
    prepared_estimated_to_actual_ratio:
      preparedTokenComparison.estimatedToActualRatio,
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

// Byte and item attributes only: attributes whose key contains "tokens" do not
// reach Sentry Logs, and estimated tokens are ceil(bytes / 4) anyway.
function logCompactionSkipped(params: {
  logger: Logger;
  trigger: CompactionTrigger;
  reason: CompactionSkipReason;
  before: StoredContextEstimate;
  foldable: FoldableContextEstimate;
  projected: StoredContextEstimate | undefined;
}): void {
  const { logger, trigger, reason, before, foldable, projected } = params;
  logger.info("coach compaction skipped", {
    trigger,
    reason,
    before_bytes: before.bytes,
    before_items: before.itemCount,
    foldable_bytes: foldable.bytes,
    foldable_items: foldable.itemCount,
    ...(projected === undefined ? {} : { projected_bytes: projected.bytes }),
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
  logger?: Logger;
};

export class DurableCompactionSession implements OpenAIResponsesCompactionAwareSession {
  private client: OpenAI | null;
  private readonly options: CompactionSessionOptions;

  constructor(options: CompactionSessionOptions) {
    this.options = options;
    this.client = options.client ?? null;
  }

  // Built on first compaction rather than in the constructor. `new OpenAI()`
  // throws when credentials are missing, and most turns never reach a
  // compaction threshold — constructing eagerly let a credential problem break
  // turns that were never going to call the API at all (issue #408).
  private getClient(): OpenAI {
    this.client ??= new OpenAI({ maxRetries: 4 });
    return this.client;
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

  // The SDK calls this after each successful turn; explicit callers pass
  // `force` to compact regardless of thresholds.
  runCompaction(
    args: OpenAIResponsesCompactionArgs = {},
  ): Promise<OpenAIResponsesCompactionResult | null> {
    return this.compactIfUseful(args, { force: args.force === true });
  }

  /**
   * Pre-turn check. Post-turn compaction never runs when a model turn fails,
   * so the orchestrator checks before every turn too. The size threshold is
   * measured on the projected request; whether compaction can help is
   * measured on stored history, the only part it rewrites.
   */
  runPreTurnCompaction(
    projected: StoredContextEstimate,
  ): Promise<OpenAIResponsesCompactionResult | null> {
    return this.compactIfUseful({}, { force: false, projected });
  }

  private async compactIfUseful(
    args: OpenAIResponsesCompactionArgs,
    request: CompactionRequest,
  ): Promise<OpenAIResponsesCompactionResult | null> {
    const startedAt = performance.now();
    const items = await this.getItems();
    if (items.length === 0) return null;

    const before = estimateStoredContext(items);
    const foldable = estimateFoldableContext(items);
    const plan = planCompaction(request, before, foldable, this.options);
    if (plan.action === "none") return null;
    if (plan.action === "skip") {
      logCompactionSkipped({
        logger: this.options.logger ?? defaultLogger,
        trigger: plan.trigger,
        reason: plan.reason,
        before,
        foldable,
        projected: request.projected,
      });
      return null;
    }

    const { trigger } = plan;
    const compactionInput = buildCompactionInput(items, (item) =>
      this.prepareHistoryItemForModelInput(item),
    );
    const preparedInputTokens = estimateSerializedTokens(compactionInput);
    const compacted = await this.getClient().responses.compact({
      ...toOpenAICompactOptions(args),
      model: this.options.model ?? "gpt-6-luna",
      input: compactionInput,
    });
    // OpenAI defines compacted.output as the canonical replacement window.
    // Persist it wholesale: individual prior messages or tool pairs are not
    // guaranteed to be retained, so splicing input items back in would undo
    // compaction and could create an invalid or duplicated conversation.
    const output = assertValidCompactionOutput(compacted.output);
    const after = estimateStoredContext(output);

    await this.options.underlyingSession.replaceAll(
      output,
      buildCompactionMetadata(trigger, before, after),
    );

    logCompactionTelemetry({
      logger: this.options.logger ?? defaultLogger,
      trigger,
      before,
      preparedInputTokens,
      after,
      latencyMs: Math.round(performance.now() - startedAt),
      casRetries: this.options.underlyingSession.getLastCasRetries?.() ?? 0,
      compacted,
    });

    return { usage: toRequestUsage(compacted) };
  }
}
