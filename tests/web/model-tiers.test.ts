import type { RetryDecision } from "@openai/agents";
import { afterEach, describe, expect, it, vi } from "vitest";

const sentryMocks = vi.hoisted(() => ({ captureMessage: vi.fn() }));

vi.mock("@sentry/nextjs", () => sentryMocks);

import {
  buildModelSettings,
  captureRateLimit,
  clampEffort,
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
  it("only resolves model and effort combinations in the allowlist", () => {
    for (const tier of resolveModelTiers()) {
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

  it("clamps a provider Retry-After to the backoff ceiling", async () => {
    const settings = buildModelSettings({
      model: "gpt-5.6-luna",
      effort: "medium",
      verbosity: "low",
    });
    const decisionFor = async (
      retryAfterMs: number,
    ): Promise<RetryDecision | undefined> =>
      settings.retry?.policy?.({
        attempt: 1,
        error: Object.assign(new Error("rate limit"), { status: 429 }),
        maxRetries: 2,
        normalized: {
          isAbort: false,
          isNetworkError: false,
          statusCode: 429,
          retryAfterMs,
        },
        stream: true,
      });

    // Under the ceiling the provider's hint is honored verbatim...
    expect(await decisionFor(1_500)).toMatchObject({
      retry: true,
      delayMs: 1_500,
    });
    // ...but a multi-minute Retry-After must not stall the serverless request.
    expect(await decisionFor(300_000)).toMatchObject({
      retry: true,
      delayMs: 8_000,
    });
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
