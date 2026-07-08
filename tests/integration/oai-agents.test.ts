/**
 * OpenAI Agents SDK integration tests — run with `bun run test:oai`.
 *
 * These tests call the real OpenAI Responses API so they require
 * OPENAI_API_KEY to be set (loaded from .env.local automatically by Vitest
 * via the dotenv plugin configured in vitest.oai.config.ts).
 *
 * They validate that our schema, model name, and input format are all accepted
 * by the Responses API before we ship changes. Each test is narrow:
 *   1. Specialist structured-output call (most likely 400 source)
 *   2. Streaming lead-coach text turn
 *   3. End-to-end: specialists → lead coach text
 */
import {
  Agent,
  Runner,
  run,
  tool,
  type AgentInputItem,
  type SessionHistoryRewriteArgs,
  type SessionHistoryRewriteAwareSession,
  type Tool,
} from "@openai/agents";
import OpenAI from "openai";
import { describe, expect, it } from "vitest";
import { z } from "zod";

import { DurableCompactionSession } from "../../lib/agent/durable-compaction-session";
import {
  type SpecialistReport,
  specialistReportSchema,
  specialistReportWireSchema,
} from "../../lib/agent/orchestration-types";
import { buildLeadCoachPrompt } from "../../lib/agent/system-prompt";
import { coachToolDefinitions } from "../../lib/agent/tools";
import { athleteContextFixture } from "../web/agent-fixtures";

const MODEL = "gpt-5-mini-2025-08-07";

const MINIMAL_USER_INPUT = [
  {
    role: "user" as const,
    content: [
      { type: "input_text" as const, text: "I ran 5km today at easy pace." },
    ],
  },
];

const RECALIBRATION_CASES = [
  {
    name: "sunny day candidate",
    athleteMessage:
      "Yes, I am fine with you re-checking my thresholds against recent hard efforts. Please recalibrate my thresholds from my latest hard 5K effort.",
    toolResult: {
      results: [
        {
          sport: "running",
          status: "candidate_queued",
          confidence: "high",
          explanation:
            "LT2 pace 260s/km -> 244s/km from an activity on 2026-07-04 (rpe 9)",
          evidence_activity_id: "run-hard-5k",
          candidate_id: "candidate-running-1",
        },
      ],
    },
  },
  {
    name: "insufficient evidence",
    athleteMessage:
      "Yes, I am fine with you re-checking my thresholds against recent hard efforts. Recheck my thresholds, but I only have easy aerobic runs recently.",
    toolResult: {
      results: [
        {
          sport: "running",
          status: "insufficient_evidence",
          confidence: null,
          explanation:
            "No recent running activity in the last 90 days looks like a hard enough effort to recalibrate from.",
          evidence_activity_id: null,
        },
      ],
    },
  },
  {
    name: "multi-sport candidates",
    athleteMessage:
      "Yes, I am fine with you re-checking my thresholds against recent hard efforts. Recalibrate both my run threshold and bike FTP from recent hard tests.",
    toolResult: {
      results: [
        {
          sport: "cycling",
          status: "candidate_queued",
          confidence: "medium",
          explanation:
            "FTP 250W -> 266W from an activity on 2026-07-03 (rpe 8)",
          evidence_activity_id: "bike-20m-test",
          candidate_id: "candidate-cycling-1",
        },
        {
          sport: "running",
          status: "candidate_queued",
          confidence: "high",
          explanation:
            "LT2 pace 260s/km -> 244s/km from an activity on 2026-07-04 (rpe 9)",
          evidence_activity_id: "run-hard-5k",
          candidate_id: "candidate-running-1",
        },
      ],
    },
  },
  {
    name: "protective edge statuses",
    athleteMessage:
      "Yes, I am fine with you re-checking my thresholds against recent hard efforts. Recalibrate my thresholds even though I manually confirmed one recently.",
    toolResult: {
      results: [
        {
          sport: "running",
          status: "already_user_confirmed",
          confidence: null,
          explanation:
            "Current threshold was manually confirmed by the athlete; recalibration will not override it.",
          evidence_activity_id: null,
        },
        {
          sport: "cycling",
          status: "cadence_gated",
          confidence: "high",
          explanation:
            "FTP 250W -> 266W from an activity on 2026-07-03 (rpe 9)",
          evidence_activity_id: "bike-20m-test",
          next_eligible_date: "2026-07-31",
        },
      ],
    },
  },
] as const;

const RECALIBRATION_CONTEXT = {
  ...athleteContextFixture,
  profile: {
    ...athleteContextFixture.profile,
    coaching_state: "active",
  },
} as const;

const RECALIBRATION_SPECIALIST_REPORTS: SpecialistReport[] = [
  {
    confidence: "high",
    proposedUpdates: [
      {
        input: "{}",
        rationale: "Athlete asked to re-check thresholds from recent efforts.",
        toolName: "recalibrate_thresholds",
      },
    ],
    risks: [],
    role: "workout",
    summary: "Athlete asked to recalibrate thresholds.",
  },
];

function recalibrationToolWithResult(result: unknown): Tool {
  const definition = coachToolDefinitions.recalibrate_thresholds;
  return tool({
    name: "recalibrate_thresholds",
    description: definition.description,
    parameters: definition.inputSchema,
    execute: () => Promise.resolve(JSON.stringify(result)),
  });
}

class InMemoryCompactionSession implements SessionHistoryRewriteAwareSession {
  constructor(private items: AgentInputItem[]) {}

  getSessionId(): Promise<string> {
    return Promise.resolve("oai-compaction-response-id-test");
  }

  getItems(limit?: number): Promise<AgentInputItem[]> {
    return Promise.resolve(
      limit === undefined ? [...this.items] : this.items.slice(-limit),
    );
  }

  addItems(items: AgentInputItem[]): Promise<void> {
    this.items.push(...items);
    return Promise.resolve();
  }

  popItem(): Promise<AgentInputItem | undefined> {
    return Promise.resolve(this.items.pop());
  }

  clearSession(): Promise<void> {
    this.items = [];
    return Promise.resolve();
  }

  replaceAll(items: AgentInputItem[]): Promise<void> {
    this.items = structuredClone(items);
    return Promise.resolve();
  }

  applyHistoryMutations(_args: SessionHistoryRewriteArgs): Promise<void> {
    return Promise.resolve();
  }
}

describe("OpenAI Agents SDK — Responses API integration", () => {
  it("accepts the specialist structured-output schema without a 400", async () => {
    const agent = new Agent({
      name: "Intake specialist",
      instructions:
        "You are an endurance coaching intake specialist. Analyse the athlete message and return a structured report. Keep summary under 50 words and proposedUpdates empty.",
      model: MODEL,
      // Matches production (lib/agent/specialists.ts): the structural-only
      // wire schema is what's actually sent to the Responses API.
      outputType: specialistReportWireSchema,
    });

    const result = await run(agent, MINIMAL_USER_INPUT, { maxTurns: 1 }).catch(
      (error: unknown) => {
        throw new Error(
          `Specialist run threw — full error: ${error instanceof Error ? error.message : String(error)}`,
        );
      },
    );

    expect(result.finalOutput).toBeTruthy();
    const parsed = specialistReportSchema.safeParse(result.finalOutput);
    expect(
      parsed.success,
      `Schema parse failed: ${JSON.stringify(parsed)}`,
    ).toBe(true);
  }, 60_000);

  it.each(RECALIBRATION_CASES)(
    "handles recalibrate_thresholds tool result: $name",
    async ({ athleteMessage, toolResult }) => {
      const agent = new Agent({
        name: "Lead coach",
        instructions: buildLeadCoachPrompt(
          RECALIBRATION_CONTEXT,
          RECALIBRATION_SPECIALIST_REPORTS,
        ),
        model: MODEL,
        tools: [recalibrationToolWithResult(toolResult)],
      });

      const result = await run(
        agent,
        [
          {
            role: "user",
            content: [{ type: "input_text", text: athleteMessage }],
          },
        ],
        { maxTurns: 3 },
      ).catch((error: unknown) => {
        throw new Error(
          `Recalibration tool run threw — full error: ${error instanceof Error ? error.message : String(error)}`,
        );
      });

      expect(
        result.history.some(
          (item) =>
            "type" in item &&
            item.type === "function_call" &&
            item.name === "recalibrate_thresholds",
        ),
        "Expected the model to call recalibrate_thresholds",
      ).toBe(true);
      expect(result.finalOutput).toEqual(expect.any(String));
      const finalOutput = result.finalOutput;
      expect(typeof finalOutput).toBe("string");
      expect(finalOutput?.length).toBeGreaterThan(0);
      expect(finalOutput).not.toMatch(/auto-apply|automatically apply/i);
    },
    60_000,
  );

  it("streams text from the lead coach without a 400", async () => {
    const agent = new Agent({
      name: "Lead coach",
      instructions:
        "You are a friendly endurance coach. Reply in one short sentence.",
      model: MODEL,
    });

    const runner = new Runner({ tracingDisabled: true });

    const chunks: string[] = [];
    let caughtError: Error | null = null;

    try {
      const result = await runner.run(agent, MINIMAL_USER_INPUT, {
        maxTurns: 1,
        stream: true,
      });

      for await (const event of result) {
        if (
          event.type === "raw_model_stream_event" &&
          typeof (event.data as Record<string, unknown>)["type"] === "string" &&
          (event.data as Record<string, unknown>)["type"] ===
            "output_text_delta"
        ) {
          const delta = (event.data as Record<string, unknown>)["delta"];
          if (typeof delta === "string") chunks.push(delta);
        }
      }
      await result.completed;
    } catch (error) {
      caughtError = error instanceof Error ? error : new Error(String(error));
    }

    expect(
      caughtError,
      `Lead coach streaming threw — full error: ${caughtError?.message}`,
    ).toBeNull();
    expect(
      chunks.join("").length,
      "Expected non-empty streamed text",
    ).toBeGreaterThan(0);
  }, 60_000);

  it("emits output_text_delta events (not response.output_text.delta)", async () => {
    const agent = new Agent({
      name: "Delta type probe",
      instructions: "Reply with exactly: OK",
      model: MODEL,
    });

    const runner = new Runner({ tracingDisabled: true });
    const result = await runner.run(agent, MINIMAL_USER_INPUT, {
      maxTurns: 1,
      stream: true,
    });

    const eventTypes = new Set<string>();
    for await (const event of result) {
      if (event.type === "raw_model_stream_event") {
        const t = (event.data as Record<string, unknown>)["type"];
        if (typeof t === "string") eventTypes.add(t);
      }
    }
    await result.completed;

    expect(
      eventTypes.has("output_text_delta"),
      `Saw event types: ${[...eventTypes].join(", ")}`,
    ).toBe(true);
    expect(eventTypes.has("response.output_text.delta")).toBe(false);
  }, 60_000);

  it("compacts with an Agents responseId without sending an unknown OpenAI parameter", async () => {
    const client = new OpenAI();
    const previous = await client.responses.create({
      model: MODEL,
      input: "Reply with exactly READY.",
      store: true,
    });
    const toolCallId = "call_compaction_pair";
    const store = new InMemoryCompactionSession([
      ...(MINIMAL_USER_INPUT as AgentInputItem[]),
      {
        type: "function_call",
        callId: toolCallId,
        name: "update_athlete_profile",
        arguments: "{}",
        status: "completed",
      } as AgentInputItem,
      {
        type: "function_call_output",
        callId: toolCallId,
        output: JSON.stringify({ status: "updated" }),
        status: "completed",
      } as unknown as AgentInputItem,
    ]);
    const session = new DurableCompactionSession({
      underlyingSession: store,
      client,
      model: MODEL,
    });

    const result = await session.runCompaction({
      force: true,
      compactionMode: "input",
      responseId: previous.id,
      store: true,
    });

    expect(result).not.toBeNull();
    const compacted = await store.getItems();
    expect(
      compacted.some((item) => "type" in item && item.type === "compaction"),
    ).toBe(true);
    const compactedRecords = compacted as Record<string, unknown>[];
    const remainingCallIds = new Set(
      compactedRecords
        .filter((item) => item["type"] === "function_call")
        .map((item) => item["call_id"])
        .filter((callId): callId is string => typeof callId === "string"),
    );
    const remainingOutputIds = new Set(
      compactedRecords
        .filter((item) => item["type"] === "function_call_output")
        .map((item) => item["call_id"])
        .filter((callId): callId is string => typeof callId === "string"),
    );
    expect(
      [...remainingCallIds].every((callId) => remainingOutputIds.has(callId)),
    ).toBe(true);
  }, 60_000);

  it("compacts a real reasoning-model run's history, including provider-attached metadata, without a 400", async () => {
    // gpt-5.4-mini (the production compaction model) is a reasoning model, so
    // a real tool-using turn against it produces reasoning items and
    // function_call/function_call_output items that the SDK stamps with its
    // own providerData — this is the actual shape stored in
    // chat_model_states.items, not a hand-built approximation of it.
    const getWeather = tool({
      name: "get_weather",
      description: "Look up the current weather for a city.",
      parameters: z.object({ city: z.string() }),
      execute: ({ city }) =>
        Promise.resolve(`${city}: 18C, light wind, clear skies.`),
    });
    const agent = new Agent({
      name: "Lead coach",
      instructions:
        "You are an endurance coach. Use the get_weather tool once, then reply in one short sentence.",
      model: MODEL,
      tools: [getWeather],
    });

    const result = await run(
      agent,
      [
        {
          role: "user",
          content: [
            {
              type: "input_text",
              text: "Should I run outside in Boulder, CO today?",
            },
          ],
        },
      ],
      { maxTurns: 3 },
    );

    const store = new InMemoryCompactionSession(result.history);
    const session = new DurableCompactionSession({
      underlyingSession: store,
      client: new OpenAI(),
      model: MODEL,
    });

    const compactionResult = await session
      .runCompaction({ force: true, compactionMode: "input" })
      .catch((error: unknown) => {
        throw new Error(
          `Compaction of a real run's history threw — full error: ${error instanceof Error ? error.message : String(error)}`,
        );
      });

    expect(compactionResult).not.toBeNull();
    const compacted = await store.getItems();
    expect(
      compacted.some((item) => "type" in item && item.type === "compaction"),
    ).toBe(true);
    // The real history must have contained a function call/output pair for
    // this to be a meaningful test of the sanitization path.
    expect(
      result.history.some(
        (item) => "type" in item && item.type === "function_call",
      ),
    ).toBe(true);
  }, 60_000);

  it("accepts a function_call namespace alongside a real reasoning item from a genuine prior response", async () => {
    // A synthetic reasoning item with a made-up id 404s ("Item ... not found
    // — items are not persisted when store is set to false"): reasoning item
    // ids are server-tracked, not client-inventable. So to test `namespace`
    // (a valid FunctionCallItem field the app doesn't send today, since it
    // has no MCP tools wired up) against real reasoning history, trigger a
    // real reasoning-model turn first and append the namespaced item to its
    // actual history, rather than hand-building the reasoning item too.
    const echo = tool({
      name: "echo_note",
      description: "Record a short note.",
      parameters: z.object({ note: z.string() }),
      execute: ({ note }) => Promise.resolve(`recorded: ${note}`),
    });
    const agent = new Agent({
      name: "Lead coach",
      instructions:
        "You are an endurance coach. Use the echo_note tool once to record a one-word note, then reply in one short sentence.",
      model: MODEL,
      tools: [echo],
    });
    const result = await run(
      agent,
      [
        {
          role: "user",
          content: [
            { type: "input_text", text: "Logging today's easy 5km run." },
          ],
        },
      ],
      { maxTurns: 3 },
    );
    expect(
      result.history.some(
        (item) => "type" in item && item.type === "reasoning",
      ),
      "Expected the real run to produce a reasoning item to test namespace against",
    ).toBe(true);

    const toolCallId = "call_namespaced_tool";
    const store = new InMemoryCompactionSession([
      ...result.history,
      {
        type: "function_call",
        callId: toolCallId,
        name: "update_athlete_profile",
        namespace: "mcp_athlete",
        arguments: "{}",
        status: "completed",
        providerData: { itemId: "fc_test_1" },
      } as unknown as AgentInputItem,
      {
        type: "function_call_output",
        callId: toolCallId,
        output: JSON.stringify({ status: "updated" }),
        status: "completed",
        providerData: { itemId: "fc_test_1" },
      } as unknown as AgentInputItem,
    ]);
    const session = new DurableCompactionSession({
      underlyingSession: store,
      client: new OpenAI(),
      model: MODEL,
    });

    const compactionResult = await session
      .runCompaction({ force: true, compactionMode: "input" })
      .catch((error: unknown) => {
        throw new Error(
          `Compaction of a namespaced function_call alongside real reasoning history threw — full error: ${error instanceof Error ? error.message : String(error)}`,
        );
      });

    expect(compactionResult).not.toBeNull();
    const compacted = await store.getItems();
    expect(
      compacted.some((item) => "type" in item && item.type === "compaction"),
    ).toBe(true);
  }, 60_000);
});
