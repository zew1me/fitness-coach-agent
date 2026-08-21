import type { RetryDecision } from "@openai/agents";
import { afterEach, describe, expect, it, vi } from "vitest";

const sentryMocks = vi.hoisted(() => ({ captureMessage: vi.fn() }));

vi.mock("@sentry/nextjs", () => sentryMocks);

import {
  buildModelSettings,
  captureRateLimit,
  clampEffort,
  getRetryAfterMs,
  isRateLimitError,
  MODEL_SUPPORTED_EFFORTS,
  MODEL_TIERS,
  type ModelTier,
  parseEffort,
  REASONING_EFFORT_LADDER,
  resolveModelTiers,
  stepDownEffort,
} from "../../lib/agent/model-tiers";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.clearAllMocks();
});

describe("model tier compatibility", () => {
  it("resolves the documented ladder order with supported efforts", () => {
    // Stub to empty rather than reading ambient config: `?.trim() || default` treats
    // "" as unset, so this asserts the shipped defaults regardless of the CI env.
    for (const name of [
      "OPENAI_LEAD_MODEL",
      "OPENAI_FALLBACK_MODEL_2",
      "OPENAI_FALLBACK_MODEL_3",
    ]) {
      vi.stubEnv(name, "");
    }

    const tiers = resolveModelTiers();

    expect(tiers.map((tier) => tier.model)).toEqual([
      "gpt-5.6-luna",
      "gpt-5.4-mini",
      "gpt-5.6-terra",
    ]);
    for (const tier of tiers) {
      expect(MODEL_SUPPORTED_EFFORTS[tier.model]?.has(tier.effort)).toBe(true);
    }
  });

  it("clamps every known model and effort to a supported value", () => {
    for (const [model, supported] of Object.entries(MODEL_SUPPORTED_EFFORTS)) {
      for (const effort of REASONING_EFFORT_LADDER) {
        expect(supported.has(clampEffort(model, effort))).toBe(true);
      }
    }
  });

  it("steps down across documented unsupported values", () => {
    expect(clampEffort("gpt-5.4-mini", "max")).toBe("xhigh");
    expect(clampEffort("gpt-5.6-luna", "minimal")).toBe("none");
    expect(stepDownEffort("medium", 1)).toBe("low");
    expect(stepDownEffort("none", 20)).toBe("none");
  });

  it("fails closed for an unknown model", () => {
    expect(clampEffort("gpt-4o", "high")).toBe("none");
    expect(sentryMocks.captureMessage).toHaveBeenCalledWith(
      expect.stringContaining("failed closed"),
      expect.objectContaining({ level: "warning" }),
    );
  });

  it("clamps direct callers consistently for settings and telemetry", async () => {
    const settings = buildModelSettings({
      model: "gpt-5.6-luna",
      effort: "minimal",
      verbosity: "low",
    });

    expect(settings.reasoning?.effort).toBe("none");
    await settings.retry?.policy?.({
      attempt: 1,
      error: Object.assign(new Error("rate limit"), { status: 429 }),
      maxRetries: 2,
      normalized: {
        isAbort: false,
        isNetworkError: false,
        statusCode: 429,
      },
      stream: true,
    });
    expect(sentryMocks.captureMessage).toHaveBeenCalledWith(
      "OpenAI rate limit hit",
      expect.objectContaining({
        tags: expect.objectContaining({
          model_tier: "unranked",
          reasoning_effort: "none",
        }),
      }),
    );
  });

  it("recognizes rate limits wrapped by streamed SDK errors", () => {
    const cause = Object.assign(new Error("rate limit"), {
      status: 429,
      headers: new Headers({ "Retry-After": "1.2" }),
    });
    const wrapped = new Error("stream failed", { cause });

    expect(isRateLimitError(wrapped)).toBe(true);
    expect(getRetryAfterMs(wrapped)).toBe(1_200);
  });

  it("recognizes a status-less Responses stream rate limit", () => {
    const error = new Error(
      "Rate limit reached for gpt-5.6-luna on tokens per min. Please try again in 4.68s.",
    );

    expect(isRateLimitError(error)).toBe(true);
    expect(getRetryAfterMs(error)).toBe(4_680);
  });

  it("uses the configured OpenAI retry count for agent SDK calls", () => {
    vi.stubEnv("OPENAI_MAX_RETRIES", "6");

    const settings = buildModelSettings({
      model: "gpt-5.6-luna",
      effort: "medium",
      verbosity: "low",
    });

    expect(settings.retry?.maxRetries).toBe(6);
  });

  it("allows zero configured retries", () => {
    vi.stubEnv("OPENAI_MAX_RETRIES", "0");

    const settings = buildModelSettings({
      model: "gpt-5.6-luna",
      effort: "medium",
      verbosity: "low",
    });

    expect(settings.retry?.maxRetries).toBe(0);
  });

  it.each(["", "-1", "1.5", "not-a-number"])(
    "falls back to four retries for invalid configuration %j",
    (configured) => {
      vi.stubEnv("OPENAI_MAX_RETRIES", configured);

      const settings = buildModelSettings({
        model: "gpt-5.6-luna",
        effort: "medium",
        verbosity: "low",
      });

      expect(settings.retry?.maxRetries).toBe(4);
      expect(sentryMocks.captureMessage).toHaveBeenCalledWith(
        "Invalid OpenAI max retries",
        expect.objectContaining({ level: "warning" }),
      );
    },
  );

  it("counts every retryable 429 attempt without capturing an exception", async () => {
    const settings = buildModelSettings({
      model: "gpt-5.6-luna",
      effort: "medium",
      verbosity: "low",
    });
    const policy = settings.retry?.policy;
    expect(policy).toBeDefined();
    for (const attempt of [1, 2]) {
      await policy?.({
        attempt,
        error: Object.assign(new Error("rate limit"), { status: 429 }),
        maxRetries: 2,
        normalized: {
          isAbort: false,
          isNetworkError: false,
          statusCode: 429,
          retryAfterMs: 10,
        },
        stream: true,
      });
    }

    expect(sentryMocks.captureMessage).toHaveBeenCalledTimes(2);
    expect(sentryMocks.captureMessage).toHaveBeenNthCalledWith(
      1,
      "OpenAI rate limit hit",
      expect.objectContaining({
        tags: expect.objectContaining({ outcome: "retrying" }),
      }),
    );
  });

  it("preserves an explicit provider replay-safety veto", async () => {
    const settings = buildModelSettings({
      model: "gpt-5.6-luna",
      effort: "medium",
      verbosity: "low",
    });

    const decision = await settings.retry?.policy?.({
      attempt: 1,
      error: Object.assign(new Error("response event already issued"), {
        status: 429,
      }),
      maxRetries: 2,
      normalized: {
        isAbort: false,
        isNetworkError: false,
        statusCode: 429,
      },
      providerAdvice: {
        suggested: false,
        replaySafety: "unsafe",
        reason: "response event already issued",
      },
      stream: true,
    });

    expect(decision).toMatchObject({
      retry: false,
      reason: "response event already issued",
    });
    expect(sentryMocks.captureMessage).not.toHaveBeenCalled();
  });

  it("honors bounded provider delays for rate limits and transient errors", async () => {
    const settings = buildModelSettings({
      model: "gpt-5.6-luna",
      effort: "medium",
      verbosity: "low",
    });
    const decisionFor = async (
      statusCode: 429 | 503,
      retryAfterMs?: number,
    ): Promise<RetryDecision | undefined> =>
      settings.retry?.policy?.({
        attempt: 1,
        error: Object.assign(new Error("transient error"), {
          status: statusCode,
        }),
        maxRetries: 2,
        normalized: {
          isAbort: false,
          isNetworkError: false,
          statusCode,
          ...(retryAfterMs === undefined ? {} : { retryAfterMs }),
        },
        stream: true,
      });

    expect(await decisionFor(429, 1_500)).toMatchObject({
      retry: true,
      delayMs: 1_500,
    });
    expect(await decisionFor(503, 2_500)).toMatchObject({
      retry: true,
      delayMs: 2_500,
    });
    expect(await decisionFor(503)).toMatchObject({
      retry: true,
      reason: "transient attempt 1",
    });
    // A multi-minute Retry-After must fall through to the model ladder rather
    // than stall the serverless request or retry before the provider's window.
    expect(await decisionFor(429, 300_000)).toMatchObject({
      retry: false,
    });
    expect(
      await settings.retry?.policy?.({
        attempt: 1,
        error: Object.assign(new Error("provider suggests retry"), {
          status: 429,
        }),
        maxRetries: 2,
        normalized: {
          isAbort: false,
          isNetworkError: false,
          statusCode: 429,
          retryAfterMs: 300_000,
        },
        providerAdvice: {
          suggested: true,
          replaySafety: "safe",
          retryAfterMs: 300_000,
        },
        stream: true,
      }),
    ).toMatchObject({ retry: false });

    sentryMocks.captureMessage.mockClear();
    expect(
      await settings.retry?.policy?.({
        attempt: 1,
        error: Object.assign(new Error("provider suggests retry"), {
          status: 429,
        }),
        maxRetries: 2,
        normalized: {
          isAbort: false,
          isNetworkError: false,
          statusCode: 429,
          retryAfterMs: 1_500,
        },
        providerAdvice: {
          suggested: true,
          replaySafety: "safe",
          retryAfterMs: 1_500,
        },
        stream: true,
      }),
    ).toMatchObject({ retry: true, delayMs: 1_500 });
    expect(sentryMocks.captureMessage).toHaveBeenCalledTimes(1);
  });

  it("tags an off-ladder tier as unranked rather than tier 1", () => {
    captureRateLimit({
      tier: { model: "gpt-5.6-sol", effort: "medium", verbosity: "low" },
      outcome: "exhausted",
    });
    captureRateLimit({
      tier: MODEL_TIERS[0] as ModelTier,
      outcome: "exhausted",
    });

    expect(sentryMocks.captureMessage).toHaveBeenNthCalledWith(
      1,
      "OpenAI rate limit hit",
      expect.objectContaining({
        tags: expect.objectContaining({ model_tier: "unranked" }),
      }),
    );
    expect(sentryMocks.captureMessage).toHaveBeenNthCalledWith(
      2,
      "OpenAI rate limit hit",
      expect.objectContaining({
        tags: expect.objectContaining({ model_tier: "1" }),
      }),
    );
  });

  it("normalizes valid env strings and rejects max and typos", () => {
    expect(parseEffort("MEDIUM", "low")).toBe("medium");
    expect(parseEffort("max", "low")).toBe("low");
    expect(parseEffort("medim", "medium")).toBe("medium");
    expect(parseEffort(undefined, "high")).toBe("high");
    expect(sentryMocks.captureMessage).toHaveBeenCalledTimes(2);
  });
});
