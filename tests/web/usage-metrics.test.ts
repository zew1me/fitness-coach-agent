import { Usage } from "@openai/agents";
import { beforeEach, describe, expect, it, vi } from "vitest";

const sentryMocks = vi.hoisted(() => ({
  logger: { info: vi.fn() },
}));

vi.mock("@sentry/nextjs", () => sentryMocks);

import { recordStageUsage } from "../../lib/agent/usage-metrics";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("recordStageUsage", () => {
  it("logs the largest request input as the stage request size", () => {
    const usage = new Usage({
      requests: 2,
      inputTokens: 230,
      outputTokens: 30,
      totalTokens: 260,
      requestUsageEntries: [
        {
          inputTokens: 90,
          inputTokensDetails: { cached_tokens: 20 },
          outputTokens: 10,
          outputTokensDetails: { reasoning_tokens: 4 },
          totalTokens: 100,
        },
        {
          inputTokens: 140,
          inputTokensDetails: { cached_tokens: 30 },
          outputTokens: 20,
          outputTokensDetails: { reasoning_tokens: 6 },
          totalTokens: 160,
        },
      ],
    });

    recordStageUsage("specialist", usage);

    expect(sentryMocks.logger.info).toHaveBeenCalledWith(
      "coach model stage usage",
      expect.objectContaining({
        stage: "specialist",
        input_tokens: 230,
        request_size_tokens: 140,
        max_request_input: 140,
      }),
    );
  });

  it.each([null, "20", true, { tokens: 20 }, [20]])(
    "ignores nonnumeric counters %j without losing other request usage",
    (detail) => {
      const usage = new Usage({
        requests: 3,
        inputTokens: 30,
        outputTokens: 6,
        totalTokens: 36,
        requestUsageEntries: [
          {
            inputTokens: 10,
            outputTokens: 2,
            totalTokens: 12,
            inputTokensDetails: { cached_tokens: detail },
            outputTokensDetails: { reasoning_tokens: detail },
          },
          { inputTokens: 10, outputTokens: 2, totalTokens: 12 },
          {
            inputTokens: 10,
            outputTokens: 2,
            totalTokens: 12,
            inputTokensDetails: { cached_tokens: 5 },
            outputTokensDetails: { reasoning_tokens: 1 },
          },
        ],
      });

      recordStageUsage("lead", usage);

      expect(sentryMocks.logger.info).toHaveBeenCalledWith(
        "coach model stage usage",
        expect.objectContaining({ cached_tokens: 5, reasoning_tokens: 1 }),
      );
    },
  );

  it("falls back to aggregate input when per-request usage is unavailable", () => {
    const usage = new Usage({
      requests: 1,
      inputTokens: 95_794,
      outputTokens: 100,
      totalTokens: 95_894,
    });

    recordStageUsage("lead", usage);

    expect(sentryMocks.logger.info).toHaveBeenCalledWith(
      "coach model stage usage",
      expect.objectContaining({
        stage: "lead",
        input_tokens: 95_794,
        request_size_tokens: 95_794,
        max_request_input: 0,
      }),
    );
  });

  it("does not emit a log when usage is unavailable", () => {
    recordStageUsage("delegation", undefined);

    expect(sentryMocks.logger.info).not.toHaveBeenCalled();
  });

  it("writes to an injected logger instead of Sentry", () => {
    const logger = {
      debug: vi.fn(),
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
    };
    const usage = new Usage({
      requests: 1,
      inputTokens: 10,
      outputTokens: 2,
      totalTokens: 12,
    });

    recordStageUsage("lead", usage, logger);

    expect(logger.info).toHaveBeenCalledWith(
      "coach model stage usage",
      expect.objectContaining({ stage: "lead", input_tokens: 10 }),
    );
    expect(sentryMocks.logger.info).not.toHaveBeenCalled();
  });
});
