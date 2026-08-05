import {
  retryPolicies,
  type ModelSettings,
  type ModelRetryNormalizedError,
  type RetryDecision,
  type RetryPolicy,
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

// Ladder position for telemetry, matched against the same frozen MODEL_TIERS the
// orchestrator runs. A tier that is not on the ladder — a specialist tier, or an
// env misconfiguration — is reported as "unranked" rather than silently tagged as
// tier 1, which made rate limits look like they hit the lead model.
function tierLabel(tier: ModelTier): string {
  const index = MODEL_TIERS.findIndex(
    (candidate) =>
      candidate.model === tier.model && candidate.effort === tier.effort,
  );
  return index < 0 ? "unranked" : String(index + 1);
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
      model_tier: tierLabel(options.tier),
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
// waitForRetryDelay in @openai/agents-core/runner/modelRetry). Provider delays
// within this ceiling are honored; longer delays decline the in-request retry so
// the orchestrator can fall through to the next model tier instead of stalling.
const DEFAULT_OPENAI_MAX_RETRIES = 4;
const MAX_RETRY_DELAY_MS = 8_000;

const providerSuggestedPolicy = retryPolicies.providerSuggested();

function retryTransient(
  normalized: ModelRetryNormalizedError,
  reason: string,
): RetryDecision {
  if (normalized.retryAfterMs === undefined) return { retry: true, reason };
  if (normalized.retryAfterMs > MAX_RETRY_DELAY_MS) {
    return {
      retry: false,
      reason: `${reason}; provider delay exceeds the in-request retry budget`,
    };
  }
  return { retry: true, delayMs: normalized.retryAfterMs, reason };
}

const providerSuggestedWithinRetryBudget: RetryPolicy = async (context) => {
  if (
    context.normalized.retryAfterMs !== undefined &&
    context.normalized.retryAfterMs > MAX_RETRY_DELAY_MS
  ) {
    return false;
  }
  return providerSuggestedPolicy(context);
};

function resolveMaxRetries(): number {
  const raw = process.env["OPENAI_MAX_RETRIES"];
  if (raw === undefined) return DEFAULT_OPENAI_MAX_RETRIES;

  const configured = Number(raw);
  if (
    raw.trim() !== "" &&
    Number.isSafeInteger(configured) &&
    configured >= 0
  ) {
    return configured;
  }
  captureInvalidConfiguration("Invalid OpenAI max retries", {
    configuredMaxRetries: raw,
    fallback: DEFAULT_OPENAI_MAX_RETRIES,
  });
  return DEFAULT_OPENAI_MAX_RETRIES;
}

export function buildModelSettings(tier: ModelTier): ModelSettings {
  const effectiveTier: ModelTier = {
    ...tier,
    effort: clampEffort(tier.model, tier.effort),
  };
  const transientPolicy: RetryPolicy = ({ normalized, attempt }) => {
    if (normalized.isAbort) return false;
    if (normalized.statusCode === 429) {
      return retryTransient(normalized, `429 attempt ${attempt}`);
    }
    if (normalized.statusCode === 503 || normalized.isNetworkError) {
      return retryTransient(normalized, `transient attempt ${attempt}`);
    }
    return false;
  };
  const composedPolicy = retryPolicies.any(
    providerSuggestedWithinRetryBudget,
    transientPolicy,
  );
  const observedPolicy: RetryPolicy = async (context) => {
    const decision = await composedPolicy(context);
    const willRetry =
      decision === true || (typeof decision === "object" && decision.retry);
    if (context.normalized.statusCode === 429 && willRetry) {
      captureRateLimit({
        tier: effectiveTier,
        outcome: "retrying",
        error: context.error,
        normalized: context.normalized,
        attempt: context.attempt,
        maxRetries: context.maxRetries,
      });
    }
    return decision;
  };

  return {
    reasoning: { effort: effectiveTier.effort },
    text: { verbosity: tier.verbosity },
    retry: {
      maxRetries: resolveMaxRetries(),
      backoff: {
        initialDelayMs: 500,
        maxDelayMs: MAX_RETRY_DELAY_MS,
        multiplier: 2,
        jitter: true,
      },
      policy: observedPolicy,
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
