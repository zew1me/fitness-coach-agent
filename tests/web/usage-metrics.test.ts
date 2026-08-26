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
});
