import type { UIMessage } from "ai";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  CHAT_TURN_LEASE_RENEW_INTERVAL_MS,
  streamCoachTurn,
} from "../../lib/agent/orchestrator";
import { runSpecialists } from "../../lib/agent/specialists";
import { buildLeadCoachPrompt } from "../../lib/agent/system-prompt";

import { athleteContextFixture } from "./agent-fixtures";

type AgentEvent = {
  data?: Record<string, unknown>;
  item?: { rawItem?: Record<string, unknown>; output?: unknown };
  name?: string;
  type: string;
};

type CoachToolForTest = {
  isEnabled: (args: {
    runContext: { context: Record<string, unknown> };
  }) => boolean;
  name: string;
};

const orchestratorMocks = vi.hoisted(() => {
  const agentConfigs: Array<Record<string, unknown>> = [];
  const events: AgentEvent[] = [];
  const runEventSequences: AgentEvent[][] = [];
  const agentsRun = vi.fn(() =>
    Promise.resolve({
      completed: Promise.resolve(),
      finalOutput: "Keep tomorrow easy.",
      output: [{ role: "assistant", content: "result output" }],
      state: { usage: undefined },
      *[Symbol.asyncIterator]() {
        const runEvents = runEventSequences.shift() ?? events;
        for (const event of runEvents) yield event;
      },
    }),
  );
  const toUIMessageStreamResponse = vi.fn(
    () => new Response("legacy stream", { status: 200 }),
  );
  const streamText = vi.fn(() => ({ toUIMessageStreamResponse }));
  const withTrace = vi.fn((_name: string, callback: () => Promise<unknown>) =>
    callback(),
  );

  class Agent {
    name: string;

    constructor(config: Record<string, unknown>) {
      this.name = String(config["name"]);
      agentConfigs.push(config);
    }

    on(): void {}
  }

  class Runner {
    constructor(config: Record<string, unknown>) {
      agentConfigs.push({ runnerConfig: config });
    }

    run = agentsRun;
  }

  // Mirrors the real export so `instanceof` narrowing in the orchestrator's
  // max-turns catch behaves the same way here as it does in production.
  class MaxTurnsExceededError extends Error {}

  const compact = vi.fn(() =>
    Promise.reject(new Error("compaction unavailable")),
  );

  return {
    Agent,
    MaxTurnsExceededError,
    Runner,
    agentConfigs,
    agentsRun,
    compact,
    events,
    runEventSequences,
    streamText,
    toUIMessageStreamResponse,
    withTrace,
  };
});

vi.mock("@openai/agents", () => ({
  Agent: orchestratorMocks.Agent,
  MaxTurnsExceededError: orchestratorMocks.MaxTurnsExceededError,
  MCPServerStreamableHttp: class MCPServerStreamableHttp {},
  Runner: orchestratorMocks.Runner,
  tool: vi.fn((definition: Record<string, unknown>) => ({
    ...definition,
    type: "function",
  })),
  withTrace: orchestratorMocks.withTrace,
}));

vi.mock("ai", async (importOriginal) => {
  const actual = await importOriginal<typeof import("ai")>();
  return {
    ...actual,
    convertToModelMessages: vi.fn((messages: UIMessage[]) =>
      Promise.resolve(messages),
    ),
    stepCountIs: vi.fn((count: number) => `step-count-${count}`),
    streamText: orchestratorMocks.streamText,
  };
});

vi.mock("../../lib/agent/context-slices", () => ({
  buildContextSlices: vi.fn(() => ({})),
}));

vi.mock("../../lib/agent/message-context", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("../../lib/agent/message-context")>();

  return {
    ...actual,
    selectMessagesForModel: vi.fn((messages: UIMessage[]) => messages),
  };
});

vi.mock("../../lib/agent/specialists", () => ({
  runSpecialists: vi.fn(() => Promise.resolve([])),
}));

vi.mock("../../lib/agent/system-prompt", () => ({
  buildLeadCoachPrompt: vi.fn(() => "system prompt"),
}));

// `DurableCompactionSession` builds a real `OpenAI` on its first compaction, and
// the constructor throws without credentials. Stubbing the module keeps this
// suite independent of an ambient OPENAI_API_KEY (which is how the durable
// tests silently ran against the error path on CI — issue #408) and lets the
// forced-compaction test reject on demand.
vi.mock("openai", () => ({
  default: class OpenAI {
    responses = { compact: orchestratorMocks.compact };
  },
}));

const originalFetch = globalThis.fetch;

function messages(): UIMessage[] {
  return [
    {
      id: "63ff9606-9158-43d7-a82b-d31ef9788b7d",
      parts: [{ text: "How should I train tomorrow?", type: "text" }],
      role: "user",
    },
  ];
}

function messagesWithFitAttachment(): UIMessage[] {
  return [
    {
      id: "d0e12f7d-6d2c-4c1b-8e8d-9c9a2c3d9c9a",
      parts: [
        { text: "Here's my ride from today.", type: "text" },
        {
          filename: "ride.fit",
          mediaType: "application/vnd.garmin.fit",
          type: "file",
          url: "https://cdn.example.com/ride.fit",
        },
      ],
      role: "user",
    },
  ];
}

function messagesWithZipAttachment(): UIMessage[] {
  return [
    {
      id: "e1f23a8e-7e3d-4d2c-9f9e-0d0b3d4e0d0b",
      parts: [
        { text: "Here's my Garmin export.", type: "text" },
        {
          filename: "garmin-export.zip",
          mediaType: "application/zip",
          type: "file",
          url: "https://cdn.example.com/garmin-export.zip",
        },
      ],
      role: "user",
    },
  ];
}

function messagesWithEarlierFitAttachment(): UIMessage[] {
  return [
    ...messagesWithFitAttachment(),
    {
      id: "assistant-after-upload",
      parts: [{ text: "I saved that ride.", type: "text" }],
      role: "assistant",
    },
    {
      id: "latest-user-text-only",
      parts: [{ text: "Add RPE 7 for yesterday.", type: "text" }],
      role: "user",
    },
  ];
}

function saveActivityFromTextTool(): CoachToolForTest | undefined {
  const leadCoachConfig = orchestratorMocks.agentConfigs.find(
    (config) => config["name"] === "Lead coach",
  );
  const tools = leadCoachConfig?.["tools"] as CoachToolForTest[];
  return tools.find(
    (candidate) => candidate.name === "save_activity_from_text",
  );
}

beforeEach(() => {
  orchestratorMocks.agentConfigs.length = 0;
  orchestratorMocks.events.length = 0;
  orchestratorMocks.runEventSequences.length = 0;
  orchestratorMocks.events.push({
    type: "raw_model_stream_event",
    data: { type: "output_text_delta", delta: "Keep tomorrow easy." },
  });
  globalThis.fetch = vi.fn(() =>
    Promise.resolve(new Response("{}", { status: 200 })),
  ) as unknown as typeof fetch;
  vi.clearAllMocks();
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("streamCoachTurn", () => {
  it("runs the lead coach through the Agents SDK with the existing turn limit", async () => {
    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messages(),
    });
    await response.text();

    expect(orchestratorMocks.agentsRun).toHaveBeenCalledWith(
      expect.anything(),
      expect.any(Array),
      expect.objectContaining({
        maxTurns: 4,
        stream: true,
      }),
    );
    expect(
      orchestratorMocks.agentConfigs.find(
        (config) => config["name"] === "Lead coach",
      ),
    ).toMatchObject({
      instructions: "system prompt",
      model: "gpt-5.6-luna",
      name: "Lead coach",
    });
    expect(orchestratorMocks.withTrace).toHaveBeenCalledWith(
      "fitness-coach-turn",
      expect.any(Function),
      expect.objectContaining({ groupId: "athlete-1" }),
    );
  });

  it("disables save_activity_from_text when the turn carries a fit/gpx/tcx attachment", async () => {
    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messagesWithFitAttachment(),
    });
    await response.text();

    expect(orchestratorMocks.agentsRun).toHaveBeenCalledWith(
      expect.anything(),
      expect.any(Array),
      expect.objectContaining({
        context: expect.objectContaining({ hasActivityFileAttachment: true }),
      }),
    );

    const saveActivityFromText = saveActivityFromTextTool();

    expect(
      saveActivityFromText?.isEnabled({
        runContext: {
          context: { hasActivityFileAttachment: true, toolCalled: false },
        },
      }),
    ).toBe(false);
  });

  it("disables save_activity_from_text when the turn carries a .zip attachment", async () => {
    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messagesWithZipAttachment(),
    });
    await response.text();

    expect(orchestratorMocks.agentsRun).toHaveBeenCalledWith(
      expect.anything(),
      expect.any(Array),
      expect.objectContaining({
        context: expect.objectContaining({ hasActivityFileAttachment: true }),
      }),
    );

    const saveActivityFromText = saveActivityFromTextTool();

    expect(
      saveActivityFromText?.isEnabled({
        runContext: {
          context: { hasActivityFileAttachment: true, toolCalled: false },
        },
      }),
    ).toBe(false);
  });

  it("keeps save_activity_from_text enabled when only an earlier turn has an activity attachment", async () => {
    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messagesWithEarlierFitAttachment(),
    });
    await response.text();

    expect(orchestratorMocks.agentsRun).toHaveBeenCalledWith(
      expect.anything(),
      expect.any(Array),
      expect.objectContaining({
        context: expect.objectContaining({
          hasActivityFileAttachment: false,
        }),
      }),
    );

    const saveActivityFromText = saveActivityFromTextTool();

    expect(
      saveActivityFromText?.isEnabled({
        runContext: {
          context: { hasActivityFileAttachment: false, toolCalled: false },
        },
      }),
    ).toBe(true);
  });

  it("keeps save_activity_from_text enabled when the turn has no file attachment", async () => {
    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messages(),
    });
    await response.text();

    expect(orchestratorMocks.agentsRun).toHaveBeenCalledWith(
      expect.anything(),
      expect.any(Array),
      expect.objectContaining({
        context: expect.objectContaining({
          hasActivityFileAttachment: false,
        }),
      }),
    );

    const saveActivityFromText = saveActivityFromTextTool();

    expect(
      saveActivityFromText?.isEnabled({
        runContext: {
          context: { hasActivityFileAttachment: false, toolCalled: false },
        },
      }),
    ).toBe(true);
  });

  it("streams text using the existing UI-message protocol", async () => {
    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messages(),
    });

    expect(response.headers.get("x-vercel-ai-ui-message-stream")).toBe("v1");
    await expect(response.text()).resolves.toContain("Keep tomorrow easy.");
  });

  it("persists the completed assistant UI message", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ id: "reply-row" }), { status: 200 }),
      ),
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messages(),
    });
    await response.text();

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost/api/chat/messages",
      expect.objectContaining({
        body: expect.stringContaining("Keep tomorrow easy."),
        method: "POST",
      }),
    );
  });

  it("maps Agents SDK tool events into persisted UI tool parts", async () => {
    orchestratorMocks.events.splice(
      0,
      orchestratorMocks.events.length,
      {
        type: "run_item_stream_event",
        name: "tool_called",
        item: {
          rawItem: {
            type: "function_call",
            callId: "call-1",
            name: "get_active_plan",
            arguments: "{}",
          },
        },
      },
      {
        type: "run_item_stream_event",
        name: "tool_output",
        item: {
          rawItem: {
            type: "function_call_result",
            callId: "call-1",
            name: "get_active_plan",
          },
          output: { active_plan: null },
        },
      },
      {
        type: "raw_model_stream_event",
        data: { type: "output_text_delta", delta: "No active plan." },
      },
    );
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response("{}", { status: 200 })),
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messages(),
    });
    await expect(response.text()).resolves.toContain("No active plan.");

    const persistCall = (
      fetchMock.mock.calls as unknown as Array<
        [RequestInfo | URL, RequestInit?]
      >
    ).find(([url]) => String(url).endsWith("/api/chat/messages"));
    const body = JSON.parse(
      String((persistCall?.[1] as RequestInit | undefined)?.body),
    ) as { parts: Array<Record<string, unknown>> };
    expect(body.parts).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          state: "output-available",
          toolCallId: "call-1",
          type: "tool-get_active_plan",
        }),
      ]),
    );
    expect(body.parts).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          text: "No active plan.",
          type: "text",
        }),
      ]),
    );
  });

  it("runs a tool-free acknowledgement turn when the lead tool turn streams no text", async () => {
    orchestratorMocks.runEventSequences.push(
      [
        {
          type: "run_item_stream_event",
          name: "tool_called",
          item: {
            rawItem: {
              type: "function_call",
              callId: "call-1",
              name: "update_athlete_profile",
              arguments: "{}",
            },
          },
        },
        {
          type: "run_item_stream_event",
          name: "tool_output",
          item: {
            rawItem: {
              type: "function_call_result",
              callId: "call-1",
              name: "update_athlete_profile",
            },
            output: { updated: true },
          },
        },
      ],
      [
        {
          type: "raw_model_stream_event",
          data: {
            type: "output_text_delta",
            delta: "I've updated your profile. Want to adjust anything else?",
          },
        },
      ],
    );
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response("{}", { status: 200 })),
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messages(),
    });

    await expect(response.text()).resolves.toContain(
      "I've updated your profile. Want to adjust anything else?",
    );
    expect(orchestratorMocks.agentsRun).toHaveBeenCalledTimes(2);
    expect(
      orchestratorMocks.agentConfigs.find(
        (config) => config["name"] === "Coach acknowledgement",
      ),
    ).toMatchObject({
      mcpServers: [],
      tools: [],
    });

    const persistCall = (
      fetchMock.mock.calls as unknown as Array<
        [RequestInfo | URL, RequestInit?]
      >
    ).find(([url]) => String(url).endsWith("/api/chat/messages"));
    expect(String(persistCall?.[1]?.body)).toContain(
      "I've updated your profile. Want to adjust anything else?",
    );
  });

  it("writes a deterministic tool acknowledgement when the follow-up turn is also silent", async () => {
    orchestratorMocks.runEventSequences.push(
      [
        {
          type: "run_item_stream_event",
          name: "tool_called",
          item: {
            rawItem: {
              type: "function_call",
              callId: "call-1",
              name: "update_athlete_profile",
              arguments: "{}",
            },
          },
        },
        {
          type: "run_item_stream_event",
          name: "tool_output",
          item: {
            rawItem: {
              type: "function_call_result",
              callId: "call-1",
              name: "update_athlete_profile",
            },
            output: { updated: true },
          },
        },
      ],
      [],
    );
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response("{}", { status: 200 })),
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messages(),
    });

    await expect(response.text()).resolves.toContain(
      "Thanks, I'll keep track of that information. Anything else you'd like to share with me at this time?",
    );
    expect(orchestratorMocks.agentsRun).toHaveBeenCalledTimes(2);

    const persistCall = (
      fetchMock.mock.calls as unknown as Array<
        [RequestInfo | URL, RequestInit?]
      >
    ).find(([url]) => String(url).endsWith("/api/chat/messages"));
    expect(String(persistCall?.[1]?.body)).toContain(
      "Thanks, I'll keep track of that information. Anything else you'd like to share with me at this time?",
    );
  });

  it("does not imply recalibrate_thresholds applied threshold changes in deterministic fallback", async () => {
    orchestratorMocks.runEventSequences.push(
      [
        {
          type: "run_item_stream_event",
          name: "tool_called",
          item: {
            rawItem: {
              type: "function_call",
              callId: "call-1",
              name: "recalibrate_thresholds",
              arguments: "{}",
            },
          },
        },
        {
          type: "run_item_stream_event",
          name: "tool_output",
          item: {
            rawItem: {
              type: "function_call_result",
              callId: "call-1",
              name: "recalibrate_thresholds",
            },
            output: {
              results: [
                {
                  sport: "running",
                  status: "candidate_queued",
                },
              ],
            },
          },
        },
      ],
      [],
    );
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response("{}", { status: 200 })),
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messages(),
    });

    const text = await response.text();
    expect(text).toContain(
      "I checked your thresholds against recent efforts and noted the result.",
    );
    expect(text).not.toContain("made some adjustments");

    const persistCall = (
      fetchMock.mock.calls as unknown as Array<
        [RequestInfo | URL, RequestInit?]
      >
    ).find(([url]) => String(url).endsWith("/api/chat/messages"));
    expect(String(persistCall?.[1]?.body)).toContain(
      "I checked your thresholds against recent efforts and noted the result.",
    );
  });

  it("writes the generic fallback when a no-tool turn is silent", async () => {
    orchestratorMocks.runEventSequences.push([]);
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response("{}", { status: 200 })),
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messages(),
    });

    await expect(response.text()).resolves.toContain(
      "Hey, can you remind me of where we are at?",
    );
    expect(orchestratorMocks.agentsRun).toHaveBeenCalledTimes(1);

    const persistCall = (
      fetchMock.mock.calls as unknown as Array<
        [RequestInfo | URL, RequestInit?]
      >
    ).find(([url]) => String(url).endsWith("/api/chat/messages"));
    expect(String(persistCall?.[1]?.body)).toContain(
      "Hey, can you remind me of where we are at?",
    );
  });

  it("writes a visible fallback text part when the model errors before streaming text", async () => {
    orchestratorMocks.agentsRun.mockRejectedValueOnce(new Error("rate limit"));
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response("{}", { status: 200 })),
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messages(),
      streamErrorMessage: "Coach is unavailable right now. Please try again.",
    });

    await response.text();

    const persistCall = (
      fetchMock.mock.calls as unknown as Array<
        [RequestInfo | URL, RequestInit?]
      >
    ).find(([url]) => String(url).endsWith("/api/chat/messages"));
    const body = JSON.parse(String(persistCall?.[1]?.body)) as {
      parts: Array<Record<string, unknown>>;
    };
    expect(body.parts).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          text: "Coach is unavailable right now. Please try again.",
          type: "text",
        }),
      ]),
    );
  });

  it("reports completed tool work instead of the error message when max turns is exceeded", async () => {
    // Sentry 7633993901: 12 process_uploaded_file calls ran and 11 succeeded,
    // then the runner threw MaxTurnsExceededError. The throw skipped both the
    // acknowledgement follow-up and the deterministic fallback, so the athlete
    // saw "Coach is unavailable" and every processed file was discarded.
    const maxTurns = new orchestratorMocks.MaxTurnsExceededError(
      "Max turns (4) exceeded",
    );
    // Mirrors StreamedRunResult._raiseError (@openai/agents-core result.js:252):
    // it errors the readable controller *and* rejects `completed`, then attaches
    // its own catch so the rejection is already observed. Reproducing all three
    // matters — the orchestrator swallows the iterator throw and never awaits
    // `completed`, so an unguarded rejection here would be unhandled. It isn't,
    // because the SDK guards it at the source.
    const completed = Promise.reject(maxTurns);
    completed.catch(() => {});
    const leadOutput = [{ role: "assistant", content: "processed 11 files" }];
    orchestratorMocks.agentsRun.mockImplementationOnce(() =>
      Promise.resolve({
        completed,
        finalOutput: "",
        output: leadOutput,
        state: { usage: undefined },
        *[Symbol.asyncIterator](): Generator<AgentEvent, void, unknown> {
          // A tool ran to completion before the runner hit the turn limit —
          // this is what makes `ranTool` true and routes the salvage through
          // the acknowledgement follow-up rather than the generic fallback.
          yield {
            type: "run_item_stream_event",
            name: "tool_called",
            item: {
              rawItem: {
                callId: "call-1",
                name: "process_uploaded_file",
                arguments: "{}",
              },
            },
          } as unknown as AgentEvent;
          throw maxTurns;
        },
      }),
    );
    // The acknowledgement follow-up is the second `run` call; it summarises the
    // lead's completed tool work.
    orchestratorMocks.runEventSequences.push([
      {
        type: "raw_model_stream_event",
        data: {
          type: "output_text_delta",
          delta: "I processed 11 files from your upload.",
        },
      } as unknown as AgentEvent,
    ]);
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response("{}", { status: 200 })),
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messages(),
      streamErrorMessage: "Coach is unavailable right now. Please try again.",
    });

    await response.text();

    const persistCall = (
      fetchMock.mock.calls as unknown as Array<
        [RequestInfo | URL, RequestInit?]
      >
    ).find(([url]) => String(url).endsWith("/api/chat/messages"));
    const body = JSON.parse(String(persistCall?.[1]?.body)) as {
      parts: Array<Record<string, unknown>>;
    };
    const text = body.parts
      .filter((part) => part["type"] === "text")
      .map((part) => String(part["text"]))
      .join(" ");

    expect(text).not.toContain("Coach is unavailable");
    expect(text).toContain("I processed 11 files from your upload.");
    // The follow-up must be handed the lead's completed output so the salvaged
    // tool work is what gets summarised.
    expect(orchestratorMocks.agentsRun).toHaveBeenCalledTimes(2);
    const followupCall = (
      orchestratorMocks.agentsRun.mock.calls as unknown as unknown[][]
    )[1];
    expect(followupCall?.[1]).toEqual(leadOutput);
  });

  it("still propagates non-max-turns errors from the lead run", async () => {
    // The error has to surface from the streamed result, not from `run` itself:
    // only that path reaches the try/catch that swallows MaxTurnsExceededError,
    // so this is what proves every other error is still rethrown.
    const generic = new Error("rate limit");
    const completed = Promise.reject(generic);
    completed.catch(() => {});
    orchestratorMocks.agentsRun.mockImplementationOnce(() =>
      Promise.resolve({
        completed,
        finalOutput: "",
        output: [],
        state: { usage: undefined },
        // eslint-disable-next-line require-yield
        *[Symbol.asyncIterator](): Generator<AgentEvent, void, unknown> {
          throw generic;
        },
      }),
    );
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response("{}", { status: 200 })),
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messages(),
      streamErrorMessage: "Coach is unavailable right now. Please try again.",
    });

    await response.text();

    const persistCall = (
      fetchMock.mock.calls as unknown as Array<
        [RequestInfo | URL, RequestInit?]
      >
    ).find(([url]) => String(url).endsWith("/api/chat/messages"));
    expect(String(persistCall?.[1]?.body)).toContain("Coach is unavailable");
  });

  it("seeds a partial durable session when the first history page exceeds the lazy budget", async () => {
    const historyMessages = Array.from({ length: 60 }, (_, index) => ({
      id: `history-${index}`,
      parts: [{ type: "text", text: "x".repeat(20_000) }],
      role: index % 2 === 0 ? "user" : "assistant",
    }));
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "http://localhost/api/chat/model-state/lease") {
        if (init?.method === "POST") {
          return Promise.resolve(
            new Response(
              JSON.stringify({
                thread_id: "thread-1",
                version: 1,
                items: [],
                coaching_memory: [],
                compaction_metadata: {},
              }),
              { status: 200 },
            ),
          );
        }
        return Promise.resolve(new Response("{}", { status: 200 }));
      }
      if (url === "http://localhost/api/chat/model-state") {
        if (init?.method === "PUT") {
          const body = JSON.parse(String(init.body)) as {
            items: unknown[];
          };
          return Promise.resolve(
            new Response(
              JSON.stringify({
                thread_id: "thread-1",
                version: 2,
                items: body.items,
                coaching_memory: [],
                compaction_metadata: {},
              }),
              { status: 200 },
            ),
          );
        }
        return Promise.resolve(
          new Response(
            JSON.stringify({
              thread_id: "thread-1",
              version: 1,
              items: [],
              coaching_memory: [],
              compaction_metadata: {},
            }),
            { status: 200 },
          ),
        );
      }
      if (url === "http://localhost/api/chat/messages?limit=100") {
        return Promise.resolve(
          new Response(
            JSON.stringify({ messages: historyMessages, next_cursor: null }),
            { status: 200 },
          ),
        );
      }
      if (url === "http://localhost/api/chat/messages") {
        return Promise.resolve(new Response("{}", { status: 200 }));
      }
      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messages(),
      useDurableSession: true,
    });
    await response.text();

    const modelStatePut = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url) === "http://localhost/api/chat/model-state" &&
        init?.method === "PUT",
    );
    const modelStatePutBody = modelStatePut?.[1]?.body;
    expect(modelStatePutBody).toEqual(expect.any(String));
    const body = JSON.parse(String(modelStatePutBody)) as {
      items: unknown[];
    };
    expect(body.items.length).toBeGreaterThan(0);
    expect(body.items.length).toBeLessThan(historyMessages.length);
  });

  it("releases a durable-session lease when the acquired lease response has malformed JSON", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "http://localhost/api/chat/model-state/lease") {
        if (init?.method === "POST") {
          return Promise.resolve(new Response("{", { status: 200 }));
        }
        return Promise.resolve(new Response("{}", { status: 200 }));
      }
      if (url === "http://localhost/api/chat/messages") {
        return Promise.resolve(new Response("{}", { status: 200 }));
      }
      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messages(),
      useDurableSession: true,
    });
    await response.text();

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost/api/chat/model-state/lease",
      expect.objectContaining({
        body: expect.stringContaining('"lease_id"'),
        method: "DELETE",
      }),
    );
  });

  it("does not fall back to stateless execution when another turn owns the lease", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (
        url === "http://localhost/api/chat/model-state/lease" &&
        init?.method === "POST"
      ) {
        return Promise.resolve(new Response("conflict", { status: 409 }));
      }
      if (url === "http://localhost/api/chat/messages") {
        return Promise.resolve(new Response("{}", { status: 200 }));
      }
      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messages(),
      useDurableSession: true,
    });
    await response.text();

    expect(orchestratorMocks.agentsRun).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalledWith(
      "http://localhost/api/chat/model-state/lease",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  // Issue #408: durable-session setup is an optimisation. Lease acquisition
  // already degraded to a stateless turn on failure, but every step after it
  // killed the turn, so a transient 5xx reached the athlete as
  // "Coach is unavailable". Each case below asserts the turn still reaches the
  // model rather than the stream error path.
  type DegradingDurableOptions = {
    modelStateStatus?: number;
    modelStateWriteStatus?: number;
    transcriptStatus?: number;
    storedItems?: unknown[];
    historyMessages?: UIMessage[];
    onModelStateRead?: () => void;
  };

  function modelStateResponse(
    options: DegradingDurableOptions,
    init?: RequestInit,
  ): Response {
    if (init?.method === "PUT" && options.modelStateWriteStatus !== undefined) {
      return new Response("write rejected", {
        status: options.modelStateWriteStatus,
      });
    }
    options.onModelStateRead?.();
    if (options.modelStateStatus !== undefined) {
      return new Response("upstream failure", {
        status: options.modelStateStatus,
      });
    }
    return new Response(
      JSON.stringify({
        thread_id: "thread-1",
        version: 1,
        items: options.storedItems ?? [],
        coaching_memory: [],
        compaction_metadata: {},
      }),
      { status: 200 },
    );
  }

  function transcriptResponse(options: DegradingDurableOptions): Response {
    if (options.transcriptStatus !== undefined) {
      return new Response("upstream failure", {
        status: options.transcriptStatus,
      });
    }
    return new Response(
      JSON.stringify({
        messages: options.historyMessages ?? [],
        next_cursor: null,
      }),
      { status: 200 },
    );
  }

  function degradingDurableFetch(
    options: DegradingDurableOptions,
  ): ReturnType<typeof vi.fn<(...args: never[]) => Promise<Response>>> {
    return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "http://localhost/api/chat/model-state/lease") {
        return Promise.resolve(
          new Response(JSON.stringify({ thread_id: "thread-1" }), {
            status: 200,
          }),
        );
      }
      if (url === "http://localhost/api/chat/model-state") {
        return Promise.resolve(modelStateResponse(options, init));
      }
      if (url === "http://localhost/api/chat/messages?limit=100") {
        return Promise.resolve(transcriptResponse(options));
      }
      if (url === "http://localhost/api/chat/messages") {
        return Promise.resolve(new Response("{}", { status: 200 }));
      }
      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });
  }

  async function runDurableTurn(
    fetchMock: ReturnType<typeof degradingDurableFetch>,
    signal?: AbortSignal,
  ): Promise<string> {
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messages(),
      ...(signal ? { signal } : {}),
      useDurableSession: true,
    });
    return response.text();
  }

  it("degrades to a stateless turn when the model state is unreadable", async () => {
    const body = await runDurableTurn(
      degradingDurableFetch({ modelStateStatus: 503 }),
    );

    expect(orchestratorMocks.agentsRun).toHaveBeenCalled();
    expect(body).not.toContain("Coach is unavailable");
  });

  it("degrades to a stateless turn when the transcript cannot be fetched", async () => {
    const body = await runDurableTurn(
      degradingDurableFetch({ transcriptStatus: 503 }),
    );

    expect(orchestratorMocks.agentsRun).toHaveBeenCalled();
    expect(body).not.toContain("Coach is unavailable");
  });

  it("degrades to a stateless turn when forced compaction fails above the hard limit", async () => {
    // estimateStoredContext is ceil(bytes / 4), so ~1.1 MB of text clears the
    // 260 000-token hard limit where compactIfNeeded rethrows.
    const body = await runDurableTurn(
      degradingDurableFetch({
        storedItems: [
          {
            role: "user",
            content: [{ type: "input_text", text: "x".repeat(1_100_000) }],
          },
        ],
      }),
    );

    expect(orchestratorMocks.compact).toHaveBeenCalled();
    expect(orchestratorMocks.agentsRun).toHaveBeenCalled();
    expect(body).not.toContain("Coach is unavailable");
  });

  it("does not degrade to stateless execution when the turn is aborted during setup", async () => {
    const controller = new AbortController();
    await runDurableTurn(
      degradingDurableFetch({
        modelStateStatus: 503,
        onModelStateRead: () => controller.abort(),
      }),
      controller.signal,
    );

    expect(orchestratorMocks.agentsRun).not.toHaveBeenCalled();
  });

  // One older message so seeding actually writes (`addItems` → PUT model-state);
  // it must not be in the current turn or `initializeSessionFromTranscript`
  // filters it out and seeds nothing.
  function priorHistoryMessages(): UIMessage[] {
    return [
      {
        id: "0f1e2d3c-4b5a-4968-8776-655443332211",
        parts: [{ text: "Yesterday's ride felt easy.", type: "text" }],
        role: "user",
      },
    ];
  }

  it("does not degrade to stateless execution when a durable setup write hits a lease conflict", async () => {
    // A 409 survives the CAS retries only when the lease check itself rejects
    // the write — another turn owns this chat's durable state, so this turn
    // must end rather than carry on statelessly.
    await runDurableTurn(
      degradingDurableFetch({
        historyMessages: priorHistoryMessages(),
        modelStateWriteStatus: 409,
      }),
    );

    expect(orchestratorMocks.agentsRun).not.toHaveBeenCalled();
  });

  it("degrades to a stateless turn when a durable setup write fails for a non-conflict reason", async () => {
    const body = await runDurableTurn(
      degradingDurableFetch({
        historyMessages: priorHistoryMessages(),
        modelStateWriteStatus: 503,
      }),
    );

    expect(orchestratorMocks.agentsRun).toHaveBeenCalled();
    expect(body).not.toContain("Coach is unavailable");
  });

  it("passes an abort signal to durable pre-run fetches", async () => {
    const controller = new AbortController();
    let leaseSignal: AbortSignal | undefined;
    let historySignal: AbortSignal | undefined;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "http://localhost/api/chat/model-state/lease") {
        if (init?.method === "POST") {
          leaseSignal = init.signal ?? undefined;
          return Promise.resolve(
            new Response(
              JSON.stringify({
                thread_id: "thread-1",
                version: 1,
                items: [],
                coaching_memory: [],
                compaction_metadata: {},
              }),
              { status: 200 },
            ),
          );
        }
        return Promise.resolve(new Response("{}", { status: 200 }));
      }
      if (url === "http://localhost/api/chat/model-state") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              thread_id: "thread-1",
              version: 1,
              items: [],
              coaching_memory: [],
              compaction_metadata: {},
            }),
            { status: 200 },
          ),
        );
      }
      if (url === "http://localhost/api/chat/messages?limit=100") {
        historySignal = init?.signal ?? undefined;
        return Promise.resolve(
          new Response(JSON.stringify({ messages: [], next_cursor: null }), {
            status: 200,
          }),
        );
      }
      if (url === "http://localhost/api/chat/messages") {
        return Promise.resolve(new Response("{}", { status: 200 }));
      }
      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messages(),
      signal: controller.signal,
      useDurableSession: true,
    });
    await response.text();

    expect(leaseSignal).toBeInstanceOf(AbortSignal);
    expect(historySignal).toBeInstanceOf(AbortSignal);
    controller.abort();
    expect(leaseSignal?.aborted).toBe(true);
    expect(historySignal?.aborted).toBe(true);
  });

  it("degrades to lead-only when runSpecialists rejects", async () => {
    // runSpecialists() itself handles per-specialist failures internally
    // (see lib/agent/specialists.ts) and should never reject in practice,
    // but if it somehow does, the turn must still survive and run the lead
    // coach rather than killing the turn and surfacing an empty assistant
    // bubble.
    vi.mocked(runSpecialists).mockRejectedValueOnce(
      new Error("Specialist run crashed unexpectedly"),
    );

    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messages(),
    });

    // Turn survives: lead coach still runs and streams a response.
    await expect(response.text()).resolves.toContain("Keep tomorrow easy.");
    expect(orchestratorMocks.agentsRun).toHaveBeenCalled();
    expect(
      orchestratorMocks.agentConfigs.find(
        (config) => config["name"] === "Lead coach",
      ),
    ).toBeDefined();
  });

  it("passes the specialist reports runSpecialists returns through to the lead coach prompt", async () => {
    // runSpecialists() is trusted to return already-valid reports (schema
    // validation and per-specialist repair happen inside it), so the
    // orchestrator should pass them through to buildLeadCoachPrompt as-is.
    vi.mocked(runSpecialists).mockResolvedValueOnce([
      {
        confidence: "low",
        proposedUpdates: [],
        role: "workout",
        risks: [],
        summary: "Recalibrate thresholds.",
      },
    ] as unknown as Awaited<ReturnType<typeof runSpecialists>>);

    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messages(),
    });

    await expect(response.text()).resolves.toContain("Keep tomorrow easy.");
    // buildLeadCoachPrompt is mocked to a static string in this file, so
    // assert on what it was called with rather than the rendered prompt.
    const reportsPassedToLeadCoach =
      vi.mocked(buildLeadCoachPrompt).mock.calls[0]?.[1];
    expect(reportsPassedToLeadCoach).toEqual([
      expect.objectContaining({ summary: "Recalibrate thresholds." }),
    ]);
  });

  it("no longer validates runSpecialists' return value against a schema before use", async () => {
    // Regression guard: the orchestrator used to run
    // specialistReportsSchema.parse() over runSpecialists' return value and
    // would throw (degrading to lead-only) on any malformed report. That
    // validation moved inside runSpecialists itself (see specialists.ts),
    // so the orchestrator must now trust and forward whatever array
    // runSpecialists resolves with, even a structurally invalid one, rather
    // than re-validating or discarding it.
    const malformedReports = [{ notAValidSpecialistReportShape: true }];
    vi.mocked(runSpecialists).mockResolvedValueOnce(
      malformedReports as unknown as Awaited<ReturnType<typeof runSpecialists>>,
    );

    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: messages(),
    });

    // The turn does not throw or degrade to an empty reports list — the
    // malformed value is passed straight through.
    await expect(response.text()).resolves.toContain("Keep tomorrow easy.");
    const reportsPassedToLeadCoach =
      vi.mocked(buildLeadCoachPrompt).mock.calls[0]?.[1];
    expect(reportsPassedToLeadCoach).toEqual(malformedReports);
  });

  it("aborts the run and skips release when a lease renewal reports the lease was lost (409)", async () => {
    vi.useFakeTimers();
    try {
      const renewCalls: Array<RequestInit | undefined> = [];
      const fetchMock = vi.fn(
        (input: RequestInfo | URL, init?: RequestInit) => {
          const url = String(input);
          if (url === "http://localhost/api/chat/model-state/lease") {
            if (init?.method === "PATCH") {
              renewCalls.push(init);
              return Promise.resolve(new Response("conflict", { status: 409 }));
            }
            return Promise.resolve(new Response("{}", { status: 200 }));
          }
          if (url === "http://localhost/api/chat/messages") {
            return Promise.resolve(new Response("{}", { status: 200 }));
          }
          return Promise.reject(new Error(`Unexpected fetch to ${url}`));
        },
      );
      globalThis.fetch = fetchMock as unknown as typeof fetch;

      // Simulate a lead-coach run that hangs (as a real streamed run would)
      // until the internal abort signal fires, then rejects — this is how
      // the real Runner behaves when its AbortSignal fires mid-stream.
      orchestratorMocks.agentsRun.mockImplementationOnce(
        (...args: unknown[]) => {
          const options = args[2] as { signal: AbortSignal };
          return Promise.resolve({
            completed: new Promise((_resolve, reject) => {
              options.signal.addEventListener("abort", () => {
                reject(new Error("run aborted: lease lost"));
              });
            }),
            state: { usage: undefined },
            [Symbol.asyncIterator]: () => ({
              next: (): Promise<IteratorResult<unknown>> =>
                Promise.resolve({ done: true, value: undefined }),
            }),
          }) as unknown as ReturnType<typeof orchestratorMocks.agentsRun>;
        },
      );

      const response = streamCoachTurn({
        accessToken: "token-1",
        acquiredLease: { thread_id: "thread-1" },
        baseUrl: "http://localhost",
        context: athleteContextFixture,
        leaseId: "lease-1",
        messages: messages(),
      });
      const textPromise = response.text();

      // Fire one lease-renewal tick; the mocked fetch answers it with 409.
      await vi.advanceTimersByTimeAsync(CHAT_TURN_LEASE_RENEW_INTERVAL_MS);

      const text = await textPromise;

      expect(renewCalls).toHaveLength(1);
      expect(text).toContain("Coach is unavailable");
      expect(fetchMock).not.toHaveBeenCalledWith(
        "http://localhost/api/chat/model-state/lease",
        expect.objectContaining({ method: "DELETE" }),
      );
    } finally {
      vi.useRealTimers();
    }
  });
});
