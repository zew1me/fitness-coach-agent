import type { UIMessage } from "ai";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { streamCoachTurn } from "../../lib/agent/orchestrator";

import { athleteContextFixture } from "./agent-fixtures";

type AgentEvent = {
  data?: Record<string, unknown>;
  item?: { rawItem?: Record<string, unknown>; output?: unknown };
  name?: string;
  type: string;
};

const orchestratorMocks = vi.hoisted(() => {
  const agentConfigs: Array<Record<string, unknown>> = [];
  const events: AgentEvent[] = [];
  const agentsRun = vi.fn(() =>
    Promise.resolve({
      completed: Promise.resolve(),
      finalOutput: "Keep tomorrow easy.",
      state: { usage: undefined },
      *[Symbol.asyncIterator]() {
        for (const event of events) yield event;
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

  return {
    Agent,
    Runner,
    agentConfigs,
    agentsRun,
    events,
    streamText,
    toUIMessageStreamResponse,
    withTrace,
  };
});

vi.mock("@openai/agents", () => ({
  Agent: orchestratorMocks.Agent,
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

beforeEach(() => {
  orchestratorMocks.agentConfigs.length = 0;
  orchestratorMocks.events.length = 0;
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
      model: "gpt-5.4-mini",
      name: "Lead coach",
    });
    expect(orchestratorMocks.withTrace).toHaveBeenCalledWith(
      "fitness-coach-turn",
      expect.any(Function),
      expect.objectContaining({ groupId: "athlete-1" }),
    );
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
        data: { type: "response.output_text.delta", delta: "No active plan." },
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
    await response.text();

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
  });
});
