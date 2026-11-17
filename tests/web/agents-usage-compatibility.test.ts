import { Agent, OpenAIResponsesModel, Runner } from "@openai/agents";
import OpenAI from "openai";
import { describe, expect, it, vi } from "vitest";

import { recordStageUsage } from "../../lib/agent/usage-metrics";

describe("Agents SDK usage compatibility", () => {
  // Sentry PYTHON-FASTAPI-17: numeric-only SDK schemas aborted the turn after
  // text streamed, when OpenAI returned nonnumeric cache-write metadata.
  it.each([
    { ephemeral_5m_input_tokens: 0, ephemeral_1h_input_tokens: 0 },
    null,
  ])(
    "completes a streamed response with cache-write details %j",
    async (cacheWriteDetails) => {
      const inputDetails = {
        cached_tokens: 20,
        cache_write_tokens_details: cacheWriteDetails,
      };
      const outputDetails = { reasoning_tokens: 4 };
      const text = "Keep today's ride easy.";
      const events = [
        { type: "response.output_text.delta", delta: text },
        {
          type: "response.completed",
          response: {
            id: "resp_usage_regression",
            object: "response",
            status: "completed",
            output: [
              {
                id: "msg_usage_regression",
                type: "message",
                role: "assistant",
                status: "completed",
                content: [{ type: "output_text", text, annotations: [] }],
              },
            ],
            usage: {
              input_tokens: 100,
              output_tokens: 10,
              total_tokens: 110,
              input_tokens_details: inputDetails,
              output_tokens_details: outputDetails,
            },
          },
        },
      ];
      const fetchMock = vi.fn(() =>
        Promise.resolve(
          new Response(
            events
              .map(
                (event) =>
                  `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`,
              )
              .join(""),
            { headers: { "content-type": "text/event-stream" } },
          ),
        ),
      );
      const client = new OpenAI({ apiKey: "test-key", fetch: fetchMock });
      const agent = new Agent({
        name: "Usage regression coach",
        instructions: "Be concise.",
        model: new OpenAIResponsesModel(client, "gpt-5.4-mini"),
      });
      const result = await new Runner({ tracingDisabled: true }).run(
        agent,
        "What should I do today?",
        { stream: true },
      );
      const deltas: string[] = [];
      for await (const event of result) {
        if (
          event.type === "raw_model_stream_event" &&
          event.data.type === "output_text_delta"
        ) {
          deltas.push(event.data.delta);
        }
      }
      await result.completed;

      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(deltas.join("")).toBe(text);
      expect(result.finalOutput).toBe(text);
      expect(
        result.state.usage.requestUsageEntries?.[0]?.inputTokensDetails,
      ).toEqual(inputDetails);
      const logger = {
        debug: vi.fn(),
        info: vi.fn(),
        warn: vi.fn(),
        error: vi.fn(),
      };
      recordStageUsage("lead", result.state.usage, logger);
      expect(logger.info).toHaveBeenCalledWith(
        "coach model stage usage",
        expect.objectContaining({
          request_count: 1,
          input_tokens: 100,
          output_tokens: 10,
          total_tokens: 110,
          cached_tokens: 20,
          reasoning_tokens: 4,
        }),
      );
    },
  );
});
