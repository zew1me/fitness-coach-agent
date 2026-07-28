import type {
  ModelSettings,
  ModelRetryNormalizedError,
  RetryDecision,
} from "@openai/agents";
import * as Sentry from "@sentry/nextjs";

import { modelCircuitBreaker } from "./model-circuit-breaker";

export const REASONING_EFFORT_LADDER = [
  "none",
  "minimal",
  "low",
  "medium",
  "high",
  "xhigh",
] as const;

type ReasoningEffort = (typeof REASONING_EFFORT_LADDER)[number];
type ModelSettingsReasoningEffort = Exclude<
  NonNullable<ModelSettings["reasoning"]>["effort"],
  null | undefined
>;
type TextVerbosity = "low" | "medium" | "high";

const STANDARD_REASONING_EFFORTS: ReadonlySet<ModelSettingsReasoningEffort> =
  new Set(["none", "low", "medium", "high", "xhigh"]);

export const MODEL_SUPPORTED_EFFORTS: Record<
  string,
  ReadonlySet<ModelSettingsReasoningEffort>
> = {
  "gpt-5.4-mini": STANDARD_REASONING_EFFORTS,
  "gpt-5.6-luna": STANDARD_REASONING_EFFORTS,
  "gpt-5.6-sol": STANDARD_REASONING_EFFORTS,
  "gpt-5.6-terra": STANDARD_REASONING_EFFORTS,
};

export type ModelTier = {
  effort: ModelSettingsReasoningEffort;
  model: string;
  verbosity: TextVerbosity;
};

type RateLimitOutcome = "retrying" | "falling_back" | "exhausted";

type RateLimitError = {
  request_id?: unknown;
  requestID?: unknown;
  status?: unknown;
  statusCode?: unknown;
  headers?: unknown;
};

function captureInvalidConfiguration(
  message: string,
  extra: Record<string, unknown>,
): void {
  Sentry.captureMessage(message, { level: "warning", extra });
}

export function parseEffort(
  raw: string | undefined,
  fallback: ReasoningEffort,
): ReasoningEffort {
  if (raw === undefined) return fallback;
  const normalized = raw.trim().toLowerCase();
  if ((REASONING_EFFORT_LADDER as readonly string[]).includes(normalized)) {
    return normalized as ReasoningEffort;
  }
  captureInvalidConfiguration("Invalid OpenAI reasoning effort", {
    configuredEffort: raw,
    fallback,
  });
  return fallback;
}

export function clampEffort(
  model: string,
  desired: ReasoningEffort | "max",
): ModelSettingsReasoningEffort {
  const supported = MODEL_SUPPORTED_EFFORTS[model];
  if (!supported) {
    captureInvalidConfiguration(
      "Unknown OpenAI model; reasoning effort failed closed",
      {
        model,
        desired,
      },
    );
    return "none";
  }

  let index =
    desired === "max"
      ? REASONING_EFFORT_LADDER.length - 1
      : REASONING_EFFORT_LADDER.indexOf(desired);
  while (index >= 0) {
    const candidate = REASONING_EFFORT_LADDER[index];
    if (candidate !== undefined && supported.has(candidate)) return candidate;
    index -= 1;
  }
  return "none";
}

export function stepDownEffort(
  effort: ReasoningEffort,
  levels: number,
): ReasoningEffort {
  const index = REASONING_EFFORT_LADDER.indexOf(effort);
  return (
    REASONING_EFFORT_LADDER[Math.max(0, index - Math.max(0, levels))] ?? "none"
  );
}

function parseVerbosity(
  raw: string | undefined,
  fallback: TextVerbosity,
): TextVerbosity {
  if (raw === undefined) return fallback;
  const normalized = raw.trim().toLowerCase();
  if (
    normalized === "low" ||
    normalized === "medium" ||
    normalized === "high"
  ) {
    return normalized;
  }
  captureInvalidConfiguration("Invalid OpenAI text verbosity", {
    configuredVerbosity: raw,
    fallback,
  });
  return fallback;
}

export function resolveModelTiers(): readonly ModelTier[] {
  const baseline = parseEffort(
    process.env["OPENAI_LEAD_REASONING_EFFORT"],
    "medium",
  );
  const verbosity = parseVerbosity(process.env["OPENAI_TEXT_VERBOSITY"], "low");
  const model1 = process.env["OPENAI_LEAD_MODEL"]?.trim() || "gpt-5.6-luna";
  const model2 =
    process.env["OPENAI_FALLBACK_MODEL_2"]?.trim() || "gpt-5.4-mini";
  const model3 =
    process.env["OPENAI_FALLBACK_MODEL_3"]?.trim() || "gpt-5.6-terra";
  const effort2 = parseEffort(
    process.env["OPENAI_FALLBACK_REASONING_EFFORT_2"],
    baseline,
  );
  const effort3 = parseEffort(
    process.env["OPENAI_FALLBACK_REASONING_EFFORT_3"],
    stepDownEffort(baseline, 1),
  );

  return [
    { model: model1, effort: clampEffort(model1, baseline), verbosity },
    { model: model2, effort: clampEffort(model2, effort2), verbosity },
    { model: model3, effort: clampEffort(model3, effort3), verbosity },
  ] as const;
}

export function resolveSpecialistTier(effectiveTier: ModelTier): ModelTier {
  const primaryModel =
    process.env["OPENAI_LEAD_MODEL"]?.trim() || "gpt-5.6-luna";
  const model =
    effectiveTier.model === primaryModel
      ? process.env["OPENAI_SPECIALIST_MODEL"]?.trim() || effectiveTier.model
      : effectiveTier.model;
  const desired =
    effectiveTier.model === primaryModel
      ? parseEffort(process.env["OPENAI_SPECIALIST_REASONING_EFFORT"], "medium")
      : effectiveTier.effort;
  return {
    model,
    effort: clampEffort(model, desired),
    verbosity: parseVerbosity(
      process.env["OPENAI_SPECIALIST_TEXT_VERBOSITY"],
      "low",
    ),
  };
}

function tierIndex(tier: ModelTier): number {
  const index = resolveModelTiers().findIndex(
    (candidate) =>
      candidate.model === tier.model && candidate.effort === tier.effort,
  );
  return index < 0 ? 0 : index;
}

export function captureRateLimit(options: {
  tier: ModelTier;
  outcome: RateLimitOutcome;
  error?: unknown;
  normalized?: ModelRetryNormalizedError;
  attempt?: number;
  maxRetries?: number;
  fallbackModel?: string | null;
  textStarted?: boolean;
}): void {
  const error = (options.error ?? {}) as RateLimitError;
  Sentry.captureMessage("OpenAI rate limit hit", {
    level: "warning",
    tags: {
      provider: "openai",
      model: options.tier.model,
      reasoning_effort: String(options.tier.effort),
      model_tier: String(tierIndex(options.tier) + 1),
      breaker_state: modelCircuitBreaker.snapshot(options.tier.model).state,
      outcome: options.outcome,
    },
    extra: {
      requestId: error.request_id ?? error.requestID,
      attempt: options.attempt,
      maxRetries: options.maxRetries,
      retryAfterMs:
        options.normalized?.retryAfterMs ?? getRetryAfterMs(options.error),
      fallbackModel: options.fallbackModel ?? null,
      textStarted: options.textStarted ?? false,
    },
  });
}

// The SDK only applies `backoff.maxDelayMs` to its own computed exponential delay;
// an explicit `delayMs` on a RetryDecision is awaited verbatim (see
// waitForRetryDelay in @openai/agents-core/runner/modelRetry). A provider
// Retry-After of minutes would therefore stall the whole serverless request, so
// the 429 policy clamps to this same ceiling.
const MAX_RETRY_DELAY_MS = 8_000;

export function buildModelSettings(tier: ModelTier): ModelSettings {
  return {
    reasoning: { effort: tier.effort },
    text: { verbosity: tier.verbosity },
    retry: {
      maxRetries: 2,
      backoff: {
        initialDelayMs: 500,
        maxDelayMs: MAX_RETRY_DELAY_MS,
        multiplier: 2,
        jitter: true,
      },
      policy: ({ normalized, error, attempt, maxRetries }): RetryDecision => {
        if (normalized.isAbort) return false;
        if (normalized.statusCode === 429) {
          captureRateLimit({
            tier,
            outcome: "retrying",
            error,
            normalized,
            attempt,
            maxRetries,
          });
          return normalized.retryAfterMs === undefined
            ? { retry: true, reason: `429 attempt ${attempt}` }
            : {
                retry: true,
                delayMs: Math.min(normalized.retryAfterMs, MAX_RETRY_DELAY_MS),
                reason: `429 attempt ${attempt}`,
              };
        }
        if (normalized.statusCode === 503 || normalized.isNetworkError) {
          return { retry: true, reason: `transient attempt ${attempt}` };
        }
        return false;
      },
    },
  };
}

export function isRateLimitError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const candidate = error as RateLimitError;
  return candidate.status === 429 || candidate.statusCode === 429;
}

function retryAfterHeader(headers: unknown): string | undefined {
  if (headers instanceof Headers)
    return headers.get("retry-after") ?? undefined;
  if (!headers || typeof headers !== "object") return undefined;
  const record = headers as Record<string, unknown>;
  const raw = record["retry-after"] ?? record["Retry-After"];
  return typeof raw === "string" ? raw : undefined;
}

export function getRetryAfterMs(error: unknown): number | undefined {
  if (!error || typeof error !== "object") return undefined;
  const value = retryAfterHeader((error as RateLimitError).headers);
  if (!value) return undefined;
  const seconds = Number(value);
  if (Number.isFinite(seconds)) return Math.max(0, seconds * 1_000);
  const date = Date.parse(value);
  return Number.isNaN(date) ? undefined : Math.max(0, date - Date.now());
}

export const MODEL_TIERS = resolveModelTiers();
