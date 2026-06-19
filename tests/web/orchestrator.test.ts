import type { UIMessage } from "ai";
import { afterEach, describe, expect, it, vi } from "vitest";

import { streamCoachTurn } from "../../lib/agent/orchestrator";

import { athleteContextFixture } from "./agent-fixtures";

const orchestratorMocks = vi.hoisted(() => {
  const toUIMessageStreamResponse = vi.fn(
    () => new Response("stream", { status: 200 }),
  );
  const streamText = vi.fn(() => ({ toUIMessageStreamResponse }));
  const stepCountIs = vi.fn((count: number) => `step-count-${count}`);

  return { stepCountIs, streamText, toUIMessageStreamResponse };
});

vi.mock("@ai-sdk/openai", () => ({
  openai: vi.fn(() => "model"),
}));

vi.mock("ai", () => ({
  convertToModelMessages: vi.fn((messages: UIMessage[]) =>
    Promise.resolve(messages),
  ),
  stepCountIs: orchestratorMocks.stepCountIs,
  streamText: orchestratorMocks.streamText,
}));

vi.mock("../../lib/agent/coach-tools", () => ({
  createCoachTools: vi.fn(() => ({})),
}));

vi.mock("../../lib/agent/context-slices", () => ({
  buildContextSlices: vi.fn(() => ({})),
}));

vi.mock("../../lib/agent/intent-router", () => ({
  routeTurnIntent: vi.fn(() => ({ specialists: [] })),
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

type AssistantFinish = {
  finishReason: string;
  isAborted: boolean;
  responseMessage: UIMessage;
};

type UIStreamOptions = {
  generateMessageId: () => string;
  onError: () => string;
  onFinish: (event: AssistantFinish) => Promise<void>;
  originalMessages: UIMessage[];
};

type StreamTextOptions = {
  maxOutputTokens?: number;
  maxRetries?: number;
  prepareStep?: (options: {
    steps: Array<{ toolCalls: Array<{ toolName: string }> }>;
  }) => {
    activeTools?: string[];
    system?: string;
  };
  providerOptions?: {
    openai?: {
      reasoningEffort?: string;
      store?: boolean;
      leadTextVerbosity?: string;
    };
  };
  stopWhen?: unknown;
  timeout?: {
    chunkMs?: number;
    stepMs?: number;
    totalMs?: number;
  };
};

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.clearAllMocks();
});

describe("streamCoachTurn", () => {
  it("uses bounded lead settings", async () => {
    await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: [
        {
          id: "63ff9606-9158-43d7-a82b-d31ef9788b7d",
          parts: [{ text: "How should I train tomorrow?", type: "text" }],
          role: "user",
        },
      ],
    });

    const options = (
      orchestratorMocks.streamText.mock.calls as unknown as [
        [StreamTextOptions],
      ]
    )[0][0];
    const { openai } = await import("@ai-sdk/openai");

    expect(openai).toHaveBeenNthCalledWith(1, "gpt-5.5");
    expect(options).toMatchObject({
      maxOutputTokens: 2048,
      maxRetries: 2,
      providerOptions: {
        openai: {
          reasoningEffort: "medium",
          store: true,
          textVerbosity: "low",
        },
      },
      timeout: {
        chunkMs: 15_000,
        stepMs: 45_000,
        totalMs: 90_000,
      },
    });
  });

  it("persists assistant replies with the UI response message id", async () => {
    const messages: UIMessage[] = [
      {
        id: "63ff9606-9158-43d7-a82b-d31ef9788b7d",
        parts: [{ text: "How should I train tomorrow?", type: "text" }],
        role: "user",
      },
    ];
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
      messages,
    });

    expect(response.status).toBe(200);
    const streamOptions = (
      orchestratorMocks.toUIMessageStreamResponse.mock.calls as unknown as [
        [UIStreamOptions],
      ]
    )[0][0];
    expect(streamOptions.originalMessages).toBe(messages);
    expect(streamOptions.generateMessageId()).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
    );

    await streamOptions.onFinish({
      finishReason: "stop",
      isAborted: false,
      responseMessage: {
        id: "46db0714-d6d8-402b-a421-00b21b3a29f6",
        parts: [{ text: "Keep tomorrow easy.", type: "text" }],
        role: "assistant",
      },
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost/api/chat/messages",
      expect.objectContaining({
        body: JSON.stringify({
          id: "46db0714-d6d8-402b-a421-00b21b3a29f6",
          role: "assistant",
          parts: [{ text: "Keep tomorrow easy.", type: "text" }],
          metadata: {
            message_kind: "assistant_reply",
            finish_reason: "stop",
            client_message_id: "46db0714-d6d8-402b-a421-00b21b3a29f6",
          },
        }),
        method: "POST",
      }),
    );
  });

  it("continues after tool calls and forces the next step to be user-facing text", async () => {
    await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: [
        {
          id: "63ff9606-9158-43d7-a82b-d31ef9788b7d",
          parts: [{ text: "I can train 7 hours per week.", type: "text" }],
          role: "user",
        },
      ],
    });

    const streamOptions = (
      orchestratorMocks.streamText.mock.calls as unknown as [
        [StreamTextOptions],
      ]
    )[0][0];

    expect(orchestratorMocks.stepCountIs).toHaveBeenCalledWith(4);
    expect(streamOptions.stopWhen).toBe("step-count-4");
    expect(streamOptions.prepareStep).toBeTypeOf("function");

    const nextStep = streamOptions.prepareStep?.({
      steps: [{ toolCalls: [{ toolName: "update_athlete_profile" }] }],
    });

    expect(nextStep).toMatchObject({ activeTools: [] });
    expect(nextStep?.system).toContain("tell the athlete what changed");
    expect(nextStep?.system).toContain("continue the conversation");
  });

  it("requires a user-facing follow-up after unlisted tool calls", async () => {
    await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages: [
        {
          id: "63ff9606-9158-43d7-a82b-d31ef9788b7d",
          parts: [{ text: "Look up today's weather.", type: "text" }],
          role: "user",
        },
      ],
    });

    const streamOptions = (
      orchestratorMocks.streamText.mock.calls as unknown as [
        [StreamTextOptions],
      ]
    )[0][0];

    const nextStep = streamOptions.prepareStep?.({
      steps: [{ toolCalls: [{ toolName: "external_search" }] }],
    });

    expect(nextStep).toMatchObject({ activeTools: [] });
    expect(nextStep?.system).toContain("Write the final user-facing response");
  });
});
