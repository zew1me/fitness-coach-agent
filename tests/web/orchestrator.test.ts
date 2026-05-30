import type { UIMessage } from "ai";
import { afterEach, describe, expect, it, vi } from "vitest";

import { streamCoachTurn } from "../../lib/agent/orchestrator";

import { athleteContextFixture } from "./agent-fixtures";

const orchestratorMocks = vi.hoisted(() => {
  const toUIMessageStreamResponse = vi.fn(() => new Response("stream", { status: 200 }));
  const streamText = vi.fn(() => ({ toUIMessageStreamResponse }));

  return { streamText, toUIMessageStreamResponse };
});

vi.mock("@ai-sdk/openai", () => ({
  openai: vi.fn(() => "model"),
}));

vi.mock("ai", () => ({
  convertToModelMessages: vi.fn((messages: UIMessage[]) => Promise.resolve(messages)),
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

vi.mock("../../lib/agent/message-context", () => ({
  selectMessagesForModel: vi.fn((messages: UIMessage[]) => messages),
}));

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

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.clearAllMocks();
});

describe("streamCoachTurn", () => {
  it("persists assistant replies with the UI response message id", async () => {
    const messages: UIMessage[] = [
      {
        id: "63ff9606-9158-43d7-a82b-d31ef9788b7d",
        parts: [{ text: "How should I train tomorrow?", type: "text" }],
        role: "user",
      },
    ];
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify({ id: "reply-row" }), { status: 200 }))
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const response = await streamCoachTurn({
      accessToken: "token-1",
      baseUrl: "http://localhost",
      context: athleteContextFixture,
      messages,
    });

    expect(response.status).toBe(200);
    const streamOptions = (orchestratorMocks.toUIMessageStreamResponse.mock.calls as unknown as [
      [UIStreamOptions],
    ])[0][0];
    expect(streamOptions.originalMessages).toBe(messages);
    expect(streamOptions.generateMessageId()).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
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
          content: "Keep tomorrow easy.",
          metadata: {
            message_kind: "assistant_reply",
            finish_reason: "stop",
            client_message_id: "46db0714-d6d8-402b-a421-00b21b3a29f6",
          },
        }),
        method: "POST",
      })
    );
  });
});
