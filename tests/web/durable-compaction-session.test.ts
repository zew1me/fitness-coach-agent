import type { AgentInputItem } from "@openai/agents";
import { describe, expect, it, vi } from "vitest";

import {
  DEFAULT_AUTO_COMPACT_TOKENS,
  DurableCompactionSession,
  compareTokenEstimate,
  estimateStoredContext,
} from "../../lib/agent/durable-compaction-session";

const sentryMocks = vi.hoisted(() => ({
  logger: { info: vi.fn() },
}));

vi.mock("@sentry/nextjs", () => sentryMocks);

const userItem = (text: string): AgentInputItem => ({
  role: "user",
  content: [{ type: "input_text", text }],
});

describe("DurableCompactionSession", () => {
  it("stores compacted output exactly and records before/after metadata", async () => {
    const underlying = {
      addItems: vi.fn(),
      clearSession: vi.fn(),
      getItems: vi.fn().mockResolvedValue([userItem("old")]),
      getSessionId: vi.fn().mockResolvedValue("thread-1"),
      popItem: vi.fn(),
      replaceAll: vi.fn(),
      applyHistoryMutations: vi.fn(),
    };
    const output = [
      userItem("old"),
      {
        type: "compaction",
        encrypted_content: "opaque",
      } as unknown as AgentInputItem,
    ];
    const client = {
      responses: {
        compact: vi.fn().mockResolvedValue({
          output,
          usage: { input_tokens: 10, output_tokens: 2, total_tokens: 12 },
        }),
      },
    };
    const session = new DurableCompactionSession({
      underlyingSession: underlying,
      client: client as never,
      autoCompactTokens: 1,
    });

    const result = await session.runCompaction();

    expect(client.responses.compact).toHaveBeenCalledWith(
      expect.objectContaining({
        input: [userItem("old")],
        model: "gpt-5.6-luna",
      }),
    );
    expect(underlying.replaceAll).toHaveBeenCalledWith(
      output,
      expect.objectContaining({
        trigger: "auto",
        before_items: 1,
        after_items: 2,
      }),
    );
    expect(result?.usage.totalTokens).toBe(12);
    const estimatedInputTokens = estimateStoredContext([
      userItem("old"),
    ]).estimatedTokens;
    expect(sentryMocks.logger.info).toHaveBeenCalledWith(
      "coach compaction complete",
      expect.objectContaining({
        input_tokens: 10,
        stored_estimated_input_tokens: estimatedInputTokens,
        stored_estimate_minus_actual_tokens: estimatedInputTokens - 10,
        stored_estimate_error_percent: expect.any(Number),
        stored_estimated_to_actual_ratio: expect.any(Number),
        prepared_estimated_input_tokens: estimatedInputTokens,
        prepared_estimate_minus_actual_tokens: estimatedInputTokens - 10,
        prepared_estimate_error_percent: expect.any(Number),
        prepared_estimated_to_actual_ratio: expect.any(Number),
      }),
    );
  });

  it("normalizes SDK function_call_result items to Responses API function_call_output for compact requests", async () => {
    const underlying = {
      addItems: vi.fn(),
      clearSession: vi.fn(),
      getItems: vi.fn().mockResolvedValue([
        {
          type: "function_call_result",
          callId: "call-1",
          name: "update_athlete_profile",
          output: JSON.stringify({ status: "pending" }),
          status: "completed",
        } as AgentInputItem,
      ]),
      getSessionId: vi.fn().mockResolvedValue("thread-1"),
      popItem: vi.fn(),
      replaceAll: vi.fn(),
      applyHistoryMutations: vi.fn(),
    };
    const client = {
      responses: {
        compact: vi.fn().mockResolvedValue({
          output: [
            {
              type: "function_call_output",
              call_id: "call-1",
              output: JSON.stringify({ status: "pending" }),
              status: "completed",
            } as unknown as AgentInputItem,
          ],
          usage: { input_tokens: 10, output_tokens: 2, total_tokens: 12 },
        }),
      },
    };
    const session = new DurableCompactionSession({
      underlyingSession: underlying,
      client: client as never,
      autoCompactTokens: 1,
    });

    await session.runCompaction();

    const request = client.responses.compact.mock.calls[0]?.[0] as Record<
      string,
      unknown
    >;
    const input = request["input"] as Record<string, unknown>[] | undefined;
    expect(input).toHaveLength(1);
    expect(input?.[0]).toEqual(
      expect.objectContaining({
        type: "function_call_output",
        call_id: "call-1",
        output: JSON.stringify({ status: "pending" }),
        status: "completed",
      }),
    );
    expect(input?.[0]?.["callId"]).toBeUndefined();
  });

  it("wraps a single content-part function_call_result output object in an array and remaps its type for compact requests", async () => {
    const underlying = {
      addItems: vi.fn(),
      clearSession: vi.fn(),
      getItems: vi.fn().mockResolvedValue([
        {
          type: "function_call_result",
          callId: "call-1",
          name: "get_weather",
          output: { type: "text", text: "18C, clear skies." },
          status: "completed",
        } as unknown as AgentInputItem,
      ]),
      getSessionId: vi.fn().mockResolvedValue("thread-1"),
      popItem: vi.fn(),
      replaceAll: vi.fn(),
      applyHistoryMutations: vi.fn(),
    };
    const client = {
      responses: {
        compact: vi.fn().mockResolvedValue({
          output: [userItem("compacted")],
          usage: { input_tokens: 10, output_tokens: 2, total_tokens: 12 },
        }),
      },
    };
    const session = new DurableCompactionSession({
      underlyingSession: underlying,
      client: client as never,
      autoCompactTokens: 1,
    });

    await session.runCompaction();

    const request = client.responses.compact.mock.calls[0]?.[0] as Record<
      string,
      unknown
    >;
    const input = request["input"] as Record<string, unknown>[] | undefined;
    expect(input?.[0]?.["output"]).toEqual([
      { type: "input_text", text: "18C, clear skies." },
    ]);
  });

  it("normalizes SDK callId to Responses API call_id for compact requests", async () => {
    const underlying = {
      addItems: vi.fn(),
      clearSession: vi.fn(),
      getItems: vi.fn().mockResolvedValue([
        {
          type: "function_call",
          callId: "call-1",
          name: "update_athlete_profile",
          arguments: "{}",
          status: "completed",
        } as AgentInputItem,
      ]),
      getSessionId: vi.fn().mockResolvedValue("thread-1"),
      popItem: vi.fn(),
      replaceAll: vi.fn(),
      applyHistoryMutations: vi.fn(),
    };
    const client = {
      responses: {
        compact: vi.fn().mockResolvedValue({
          output: [
            {
              type: "function_call",
              call_id: "call-1",
              name: "update_athlete_profile",
              arguments: "{}",
              status: "completed",
            } as unknown as AgentInputItem,
          ],
          usage: { input_tokens: 10, output_tokens: 2, total_tokens: 12 },
        }),
      },
    };
    const session = new DurableCompactionSession({
      underlyingSession: underlying,
      client: client as never,
      autoCompactTokens: 1,
    });

    await session.runCompaction();

    const request = client.responses.compact.mock.calls[0]?.[0] as Record<
      string,
      unknown
    >;
    const input = request["input"] as Record<string, unknown>[] | undefined;
    expect(input).toHaveLength(1);
    expect(input?.[0]).toEqual(
      expect.objectContaining({
        type: "function_call",
        call_id: "call-1",
        name: "update_athlete_profile",
        arguments: "{}",
        status: "completed",
      }),
    );
    expect(input?.[0]?.["callId"]).toBeUndefined();
  });

  it("normalizes paired SDK function calls and outputs for compact requests", async () => {
    const underlying = {
      addItems: vi.fn(),
      clearSession: vi.fn(),
      getItems: vi.fn().mockResolvedValue([
        {
          type: "function_call",
          callId: "call-1",
          name: "update_athlete_profile",
          arguments: "{}",
          status: "completed",
        } as AgentInputItem,
        {
          type: "function_call_output",
          callId: "call-1",
          output: JSON.stringify({ status: "updated" }),
          status: "completed",
        } as unknown as AgentInputItem,
      ]),
      getSessionId: vi.fn().mockResolvedValue("thread-1"),
      popItem: vi.fn(),
      replaceAll: vi.fn(),
      applyHistoryMutations: vi.fn(),
    };
    const client = {
      responses: {
        compact: vi.fn().mockResolvedValue({
          output: [userItem("summary")],
          usage: { input_tokens: 10, output_tokens: 2, total_tokens: 12 },
        }),
      },
    };
    const session = new DurableCompactionSession({
      underlyingSession: underlying,
      client: client as never,
      autoCompactTokens: 1,
    });

    await session.runCompaction();

    const request = client.responses.compact.mock.calls[0]?.[0] as Record<
      string,
      unknown
    >;
    const input = request["input"] as Record<string, unknown>[] | undefined;
    expect(input).toHaveLength(2);
    expect(input?.[0]).toEqual(
      expect.objectContaining({
        type: "function_call",
        call_id: "call-1",
        name: "update_athlete_profile",
      }),
    );
    expect(input?.[0]?.["callId"]).toBeUndefined();
    expect(input?.[1]).toEqual(
      expect.objectContaining({
        type: "function_call_output",
        call_id: "call-1",
        output: JSON.stringify({ status: "updated" }),
      }),
    );
    expect(input?.[1]?.["callId"]).toBeUndefined();
  });

  it("propagates a conflict while replacing compacted history", async () => {
    const conflict = new Error("Unable to replace model state (409)");
    const underlying = {
      addItems: vi.fn(),
      clearSession: vi.fn(),
      getItems: vi.fn().mockResolvedValue([userItem("old")]),
      getSessionId: vi.fn().mockResolvedValue("thread-1"),
      popItem: vi.fn(),
      replaceAll: vi.fn().mockRejectedValue(conflict),
      applyHistoryMutations: vi.fn(),
    };
    const client = {
      responses: {
        compact: vi.fn().mockResolvedValue({ output: [userItem("summary")] }),
      },
    };
    const session = new DurableCompactionSession({
      underlyingSession: underlying,
      client: client as never,
      autoCompactTokens: 1,
    });

    await expect(session.runCompaction()).rejects.toBe(conflict);
    expect(underlying.replaceAll).toHaveBeenCalledTimes(1);
  });

  it("returns null and skips the API call when items is empty", async () => {
    const underlying = {
      addItems: vi.fn(),
      clearSession: vi.fn(),
      getItems: vi.fn().mockResolvedValue([]),
      getSessionId: vi.fn().mockResolvedValue("thread-1"),
      popItem: vi.fn(),
      replaceAll: vi.fn(),
      applyHistoryMutations: vi.fn(),
    };
    const client = { responses: { compact: vi.fn() } };
    const session = new DurableCompactionSession({
      underlyingSession: underlying,
      client: client as never,
      autoCompactTokens: 1,
    });

    const result = await session.runCompaction();
    expect(result).toBeNull();
    expect(client.responses.compact).not.toHaveBeenCalled();
  });

  it("returns null when token estimate is below autoCompactTokens threshold", async () => {
    const underlying = {
      addItems: vi.fn(),
      clearSession: vi.fn(),
      getItems: vi.fn().mockResolvedValue([userItem("short")]),
      getSessionId: vi.fn().mockResolvedValue("thread-1"),
      popItem: vi.fn(),
      replaceAll: vi.fn(),
      applyHistoryMutations: vi.fn(),
    };
    const client = { responses: { compact: vi.fn() } };
    const session = new DurableCompactionSession({
      underlyingSession: underlying,
      client: client as never,
      autoCompactTokens: 999_999,
      autoCompactNonUserItems: 999_999,
    });

    const result = await session.runCompaction();
    expect(result).toBeNull();
    expect(client.responses.compact).not.toHaveBeenCalled();
  });

  it("uses the TPM-safe default token threshold for user-only history", async () => {
    const belowThreshold = [userItem("x".repeat(239_000))];
    const aboveThreshold = [userItem("x".repeat(240_000))];
    expect(estimateStoredContext(belowThreshold).estimatedTokens).toBeLessThan(
      DEFAULT_AUTO_COMPACT_TOKENS,
    );
    expect(
      estimateStoredContext(aboveThreshold).estimatedTokens,
    ).toBeGreaterThanOrEqual(DEFAULT_AUTO_COMPACT_TOKENS);

    const underlying = {
      addItems: vi.fn(),
      clearSession: vi.fn(),
      getItems: vi
        .fn()
        .mockResolvedValueOnce(belowThreshold)
        .mockResolvedValueOnce(aboveThreshold),
      getSessionId: vi.fn().mockResolvedValue("thread-1"),
      popItem: vi.fn(),
      replaceAll: vi.fn(),
      applyHistoryMutations: vi.fn(),
    };
    const client = {
      responses: {
        compact: vi.fn().mockResolvedValue({
          output: [userItem("compacted")],
          usage: {
            input_tokens: 30_000,
            output_tokens: 100,
            total_tokens: 30_100,
            input_tokens_details: {},
            output_tokens_details: {},
          },
        }),
      },
    };
    const session = new DurableCompactionSession({
      underlyingSession: underlying,
      client: client as never,
    });

    await expect(session.runCompaction()).resolves.toBeNull();
    await expect(session.runCompaction()).resolves.not.toBeNull();

    expect(client.responses.compact).toHaveBeenCalledTimes(1);
  });

  it("force bypasses threshold checks and calls the API", async () => {
    const output = [userItem("compacted")];
    const underlying = {
      addItems: vi.fn(),
      clearSession: vi.fn(),
      getItems: vi.fn().mockResolvedValue([userItem("x")]),
      getSessionId: vi.fn().mockResolvedValue("thread-1"),
      popItem: vi.fn(),
      replaceAll: vi.fn(),
      applyHistoryMutations: vi.fn(),
    };
    const client = {
      responses: {
        compact: vi.fn().mockResolvedValue({
          output,
          usage: {
            input_tokens: 10,
            output_tokens: 5,
            total_tokens: 15,
            input_tokens_details: {},
            output_tokens_details: {},
          },
        }),
      },
    };
    const session = new DurableCompactionSession({
      underlyingSession: underlying,
      client: client as never,
      autoCompactTokens: 999_999,
    });

    const result = await session.runCompaction({ force: true });
    expect(result).not.toBeNull();
    expect(client.responses.compact).toHaveBeenCalledTimes(1);
    expect(underlying.replaceAll).toHaveBeenCalledWith(
      output,
      expect.objectContaining({ trigger: "forced" }),
    );
  });

  it("maps compaction request options to the OpenAI compact API shape", async () => {
    const output = [userItem("compacted")];
    const underlying = {
      addItems: vi.fn(),
      clearSession: vi.fn(),
      getItems: vi.fn().mockResolvedValue([userItem("x")]),
      getSessionId: vi.fn().mockResolvedValue("thread-1"),
      popItem: vi.fn(),
      replaceAll: vi.fn(),
      applyHistoryMutations: vi.fn(),
    };
    const client = {
      responses: {
        compact: vi.fn().mockResolvedValue({
          output,
          usage: {
            input_tokens: 10,
            output_tokens: 5,
            total_tokens: 15,
            input_tokens_details: {},
            output_tokens_details: {},
          },
        }),
      },
    };
    const session = new DurableCompactionSession({
      underlyingSession: underlying,
      client: client as never,
      autoCompactTokens: 999_999,
    });

    await session.runCompaction({
      force: true,
      compactionMode: "input",
      responseId: "resp_123",
      store: true,
    });

    // previous_response_id/responseId/store must never reach the request:
    // `input` is already the complete Supabase-backed history, so also
    // referencing the server-remembered conversation for `responseId` would
    // splice a second copy of it onto `input` — that's the exact bug behind
    // the "Duplicate item found with id ..." 400 (see
    // docs/COMPACTION_DESIGN.md and durable-compaction-session.ts).
    const request = client.responses.compact.mock.calls[0]?.[0];
    expect(request).not.toHaveProperty("previous_response_id");
    expect(request).not.toHaveProperty("force");
    expect(request).not.toHaveProperty("compactionMode");
    expect(request).not.toHaveProperty("responseId");
    expect(request).not.toHaveProperty("store");
  });

  it("strips provider metadata from compaction input items", async () => {
    const output = [userItem("compacted")];
    const underlying = {
      addItems: vi.fn(),
      clearSession: vi.fn(),
      getItems: vi.fn().mockResolvedValue([
        {
          ...userItem("x"),
          providerData: { provider: "openai" },
          providerMetadata: { provider: "openai" },
        },
      ]),
      getSessionId: vi.fn().mockResolvedValue("thread-1"),
      popItem: vi.fn(),
      replaceAll: vi.fn(),
      applyHistoryMutations: vi.fn(),
    };
    const client = {
      responses: {
        compact: vi.fn().mockResolvedValue({
          output,
          usage: {
            input_tokens: 10,
            output_tokens: 5,
            total_tokens: 15,
            input_tokens_details: {},
            output_tokens_details: {},
          },
        }),
      },
    };
    const session = new DurableCompactionSession({
      underlyingSession: underlying,
      client: client as never,
      autoCompactTokens: 999_999,
    });

    await session.runCompaction({ force: true });

    const request = client.responses.compact.mock.calls[0]?.[0];
    expect(request?.input?.[0]).toEqual(userItem("x"));
    const telemetry = sentryMocks.logger.info.mock.lastCall?.[1] as Record<
      string,
      number
    >;
    expect(telemetry["stored_estimated_input_tokens"]).toBeGreaterThan(
      telemetry["prepared_estimated_input_tokens"] ?? 0,
    );
  });

  it("strips provider metadata nested inside content parts", async () => {
    const output = [userItem("compacted")];
    const underlying = {
      addItems: vi.fn(),
      clearSession: vi.fn(),
      getItems: vi.fn().mockResolvedValue([
        {
          role: "user",
          content: [
            {
              type: "input_text",
              text: "x",
              providerData: { provider: "openai" },
            },
          ],
        } as unknown as AgentInputItem,
      ]),
      getSessionId: vi.fn().mockResolvedValue("thread-1"),
      popItem: vi.fn(),
      replaceAll: vi.fn(),
      applyHistoryMutations: vi.fn(),
    };
    const client = {
      responses: {
        compact: vi.fn().mockResolvedValue({
          output,
          usage: {
            input_tokens: 10,
            output_tokens: 5,
            total_tokens: 15,
            input_tokens_details: {},
            output_tokens_details: {},
          },
        }),
      },
    };
    const session = new DurableCompactionSession({
      underlyingSession: underlying,
      client: client as never,
      autoCompactTokens: 999_999,
    });

    await session.runCompaction({ force: true });

    const request = client.responses.compact.mock.calls[0]?.[0];
    expect(request?.input?.[0]).toEqual(userItem("x"));
  });

  it("preserves fields outside the historical whitelist, like function_call namespace", async () => {
    const output = [userItem("compacted")];
    const underlying = {
      addItems: vi.fn(),
      clearSession: vi.fn(),
      getItems: vi.fn().mockResolvedValue([
        {
          type: "function_call",
          callId: "call-1",
          name: "update_athlete_profile",
          namespace: "mcp:athlete",
          arguments: "{}",
          status: "completed",
          providerData: { provider: "openai" },
        } as unknown as AgentInputItem,
      ]),
      getSessionId: vi.fn().mockResolvedValue("thread-1"),
      popItem: vi.fn(),
      replaceAll: vi.fn(),
      applyHistoryMutations: vi.fn(),
    };
    const client = {
      responses: {
        compact: vi.fn().mockResolvedValue({
          output,
          usage: {
            input_tokens: 10,
            output_tokens: 5,
            total_tokens: 15,
            input_tokens_details: {},
            output_tokens_details: {},
          },
        }),
      },
    };
    const session = new DurableCompactionSession({
      underlyingSession: underlying,
      client: client as never,
      autoCompactTokens: 999_999,
    });

    await session.runCompaction({ force: true });

    const request = client.responses.compact.mock.calls[0]?.[0];
    expect(request?.input?.[0]).toEqual({
      type: "function_call",
      call_id: "call-1",
      name: "update_athlete_profile",
      namespace: "mcp:athlete",
      arguments: "{}",
      status: "completed",
    });
  });

  it("converts a reasoning item to the raw API's summary shape, dropping rawContent, for compact requests", async () => {
    const output = [userItem("compacted")];
    const underlying = {
      addItems: vi.fn(),
      clearSession: vi.fn(),
      getItems: vi.fn().mockResolvedValue([
        {
          type: "reasoning",
          id: "rs_1",
          content: [{ type: "input_text", text: "Weighing options." }],
          rawContent: [
            { type: "reasoning_text", text: "Full chain of thought." },
          ],
          status: "completed",
          providerData: { provider: "openai" },
        } as unknown as AgentInputItem,
      ]),
      getSessionId: vi.fn().mockResolvedValue("thread-1"),
      popItem: vi.fn(),
      replaceAll: vi.fn(),
      applyHistoryMutations: vi.fn(),
    };
    const client = {
      responses: {
        compact: vi.fn().mockResolvedValue({
          output,
          usage: {
            input_tokens: 10,
            output_tokens: 5,
            total_tokens: 15,
            input_tokens_details: {},
            output_tokens_details: {},
          },
        }),
      },
    };
    const session = new DurableCompactionSession({
      underlyingSession: underlying,
      client: client as never,
      autoCompactTokens: 999_999,
    });

    await session.runCompaction({ force: true });

    const request = client.responses.compact.mock.calls[0]?.[0];
    expect(request?.input?.[0]).toEqual({
      type: "reasoning",
      id: "rs_1",
      summary: [{ type: "summary_text", text: "Weighing options." }],
      status: "completed",
    });
  });

  it("throws when compaction returns an empty output array", async () => {
    const underlying = {
      addItems: vi.fn(),
      clearSession: vi.fn(),
      getItems: vi.fn().mockResolvedValue([userItem("x")]),
      getSessionId: vi.fn().mockResolvedValue("thread-1"),
      popItem: vi.fn(),
      replaceAll: vi.fn(),
      applyHistoryMutations: vi.fn(),
    };
    const client = {
      responses: {
        compact: vi.fn().mockResolvedValue({
          output: [],
          usage: {
            input_tokens: 1,
            output_tokens: 0,
            total_tokens: 1,
            input_tokens_details: {},
            output_tokens_details: {},
          },
        }),
      },
    };
    const session = new DurableCompactionSession({
      underlyingSession: underlying,
      client: client as never,
      autoCompactTokens: 1,
    });

    await expect(session.runCompaction()).rejects.toThrow(
      /refusing to wipe durable context/,
    );
    expect(underlying.replaceAll).not.toHaveBeenCalled();
  });
});

describe("compareTokenEstimate", () => {
  it("reports over- and under-estimates with a signed error", () => {
    expect(compareTokenEstimate(1_000, 800)).toEqual({
      estimateMinusActualTokens: 200,
      estimateErrorPercent: 25,
      estimatedToActualRatio: 1.25,
    });
    expect(compareTokenEstimate(600, 800)).toEqual({
      estimateMinusActualTokens: -200,
      estimateErrorPercent: -25,
      estimatedToActualRatio: 0.75,
    });
  });

  it("avoids dividing by zero when usage is unavailable", () => {
    expect(compareTokenEstimate(100, 0)).toEqual({
      estimateMinusActualTokens: 100,
      estimateErrorPercent: null,
      estimatedToActualRatio: null,
    });
  });
});

describe("estimateStoredContext", () => {
  it("reports serialized bytes, estimated tokens, and non-user items", () => {
    expect(
      estimateStoredContext([
        userItem("hello"),
        { role: "assistant", content: [] } as unknown as AgentInputItem,
      ]),
    ).toMatchObject({
      itemCount: 2,
      nonUserItemCount: 1,
    });
  });
});
