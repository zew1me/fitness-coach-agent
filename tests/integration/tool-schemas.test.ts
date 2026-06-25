/**
 * Zod → OpenAI Responses API schema compatibility tests.
 *
 * The Responses API is stricter than Chat Completions about JSON Schema —
 * e.g. it rejects `format: "uri"` (produced by z.string().url()).
 * These tests register every coach tool schema against the real API and
 * assert that no 400 is returned, catching incompatible Zod refinements
 * before they reach production.
 *
 * Run with: bun run test:oai
 */
import { Agent, Runner, tool } from "@openai/agents";
import { describe, expect, it } from "vitest";

import { coachToolDefinitions } from "../../lib/agent/tools";

const MODEL = "gpt-5-mini-2025-08-07";

const PING_INPUT = [
  {
    role: "user" as const,
    content: [{ type: "input_text" as const, text: "Say OK." }],
  },
];

describe("Coach tool schemas — Responses API compatibility", () => {
  it("accepts all coach tool schemas without a 400", async () => {
    const tools = Object.entries(coachToolDefinitions).map(
      ([name, definition]) =>
        tool({
          name,
          description: definition.description,
          parameters: definition.inputSchema,
          execute: () => JSON.stringify({ ok: true }),
        }),
    );

    const agent = new Agent({
      name: "Schema probe",
      instructions:
        "You have access to coaching tools but should NOT call any of them. Just reply with exactly: OK",
      model: MODEL,
      tools,
    });

    const runner = new Runner({ tracingDisabled: true });
    let caughtError: Error | null = null;

    try {
      await runner.run(agent, PING_INPUT, { maxTurns: 1 });
    } catch (error) {
      caughtError = error instanceof Error ? error : new Error(String(error));
    }

    expect(
      caughtError,
      `Tool schema rejected by Responses API: ${caughtError?.message}`,
    ).toBeNull();
  }, 60_000);

  it.each(Object.entries(coachToolDefinitions))(
    "schema for '%s' is accepted individually",
    async (name, definition) => {
      const singleTool = tool({
        name,
        description: definition.description,
        parameters: definition.inputSchema,
        execute: () => JSON.stringify({ ok: true }),
      });

      const agent = new Agent({
        name: `Schema probe — ${name}`,
        instructions: "Do not call any tools. Reply with: OK",
        model: MODEL,
        tools: [singleTool],
      });

      const runner = new Runner({ tracingDisabled: true });
      let caughtError: Error | null = null;

      try {
        await runner.run(agent, PING_INPUT, { maxTurns: 1 });
      } catch (error) {
        caughtError = error instanceof Error ? error : new Error(String(error));
      }

      expect(
        caughtError,
        `Schema for '${name}' rejected: ${caughtError?.message}`,
      ).toBeNull();
    },
    60_000,
  );
});
