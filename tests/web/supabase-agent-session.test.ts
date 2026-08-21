import {
  OpenAIResponsesModel,
  type AgentInputItem,
  type ModelRequest,
} from "@openai/agents";
import { describe, expect, it, vi } from "vitest";

import { SupabaseAgentSession } from "../../lib/agent/supabase-agent-session";

const userItem = (text: string): AgentInputItem => ({
  role: "user",
  content: [{ type: "input_text", text }],
});

type FunctionCallResultItem = Extract<
  AgentInputItem,
  { type: "function_call_result" }
>;

class InspectableResponsesModel extends OpenAIResponsesModel {
  buildInput(input: AgentInputItem[]): unknown {
    const request: ModelRequest = {
      input,
      modelSettings: {},
      tools: [],
      outputType: "text",
      handoffs: [],
      tracing: false,
    };
    return this._buildResponsesCreateRequest(request, false).requestData[
      "input"
    ];
  }
}

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("SupabaseAgentSession", () => {
  it("retries a stale CAS and atomically appends against the refreshed state", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        response({
          thread_id: "thread-1",
          version: 1,
          items: [userItem("a")],
          coaching_memory: [],
          compaction_metadata: {},
        }),
      )
      .mockResolvedValueOnce(response({ detail: "version conflict" }, 409))
      .mockResolvedValueOnce(
        response({
          thread_id: "thread-1",
          version: 2,
          items: [userItem("a"), userItem("b")],
          coaching_memory: [],
          compaction_metadata: {},
        }),
      )
      .mockResolvedValueOnce(
        response({
          thread_id: "thread-1",
          version: 3,
          items: [userItem("a"), userItem("b"), userItem("c")],
          coaching_memory: [],
          compaction_metadata: {},
        }),
      );
    const session = new SupabaseAgentSession({
      accessToken: "token",
      baseUrl: "http://localhost",
      leaseId: "lease-1",
      fetch: fetchMock,
      maxCasRetries: 2,
    });

    await session.addItems([userItem("c")]);

    const puts = fetchMock.mock.calls.filter(
      ([, init]) => init?.method === "PUT",
    );
    expect(puts).toHaveLength(2);
    expect(JSON.parse(String(puts[1]?.[1]?.body))).toMatchObject({
      expected_version: 2,
      lease_id: "lease-1",
      items: [userItem("a"), userItem("b"), userItem("c")],
    });
  });

  it("throws after exhausting maxCasRetries consecutive conflicts", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        response({
          thread_id: "thread-1",
          version: 1,
          items: [userItem("a")],
          coaching_memory: [],
          compaction_metadata: {},
        }),
      )
      .mockResolvedValue(response({ detail: "version conflict" }, 409));
    // First call returns state, all subsequent PUT calls return 409.
    fetchMock
      .mockResolvedValueOnce(
        response({
          thread_id: "thread-1",
          version: 1,
          items: [userItem("a")],
          coaching_memory: [],
          compaction_metadata: {},
        }),
      )
      .mockResolvedValue(response({ detail: "version conflict" }, 409));

    const session = new SupabaseAgentSession({
      accessToken: "token",
      baseUrl: "http://localhost",
      leaseId: "lease-1",
      fetch: fetchMock,
      maxCasRetries: 1,
    });

    await expect(session.addItems([userItem("b")])).rejects.toThrow(/409/);
  });

  it("returns no items when a non-positive limit is requested", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response({
        thread_id: "thread-1",
        version: 1,
        items: [userItem("a")],
        coaching_memory: [],
        compaction_metadata: {},
      }),
    );
    const session = new SupabaseAgentSession({
      accessToken: "token",
      baseUrl: "http://localhost",
      leaseId: "lease-1",
      fetch: fetchMock,
    });

    await expect(session.getItems(0)).resolves.toEqual([]);
    await expect(session.getItems(-1)).resolves.toEqual([]);
  });

  it("self-heals a row poisoned with duplicate item ids by keeping the first occurrence", async () => {
    const first = { ...userItem("a"), id: "msg_dup" };
    const second = { ...userItem("b"), id: "msg_dup" };
    const fetchMock = vi.fn().mockResolvedValue(
      response({
        thread_id: "thread-1",
        version: 1,
        items: [first, second, userItem("c")],
        coaching_memory: [],
        compaction_metadata: {},
      }),
    );
    const session = new SupabaseAgentSession({
      accessToken: "token",
      baseUrl: "http://localhost",
      leaseId: "lease-1",
      fetch: fetchMock,
    });

    await expect(session.getItems()).resolves.toEqual([first, userItem("c")]);
  });

  it("validates loaded model state before returning it", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response({
        thread_id: "thread-1",
        items: [],
        coaching_memory: [],
        compaction_metadata: {},
      }),
    );
    const session = new SupabaseAgentSession({
      accessToken: "token",
      baseUrl: "http://localhost",
      leaseId: "lease-1",
      fetch: fetchMock,
    });

    await expect(session.getSessionId()).rejects.toThrow();
  });

  it("removes historical image inputs before model replay while retaining extracted text", () => {
    const session = new SupabaseAgentSession({
      accessToken: "token",
      baseUrl: "http://localhost",
      leaseId: "lease-1",
    });
    const prepared = session.prepareHistoryItemForModelInput({
      role: "user",
      content: [
        { type: "input_image", image: "https://example.test/image.png" },
        { type: "input_text", text: "Extracted: recovery score 42" },
      ],
    } as AgentInputItem) as { content: Array<{ type: string; text?: string }> };

    expect(prepared.content).toEqual([
      { type: "input_text", text: "Extracted: recovery score 42" },
    ]);
  });

  it("rewrites a historical input_file to text while preserving the file link", () => {
    const session = new SupabaseAgentSession({
      accessToken: "token",
      baseUrl: "http://localhost",
      leaseId: "lease-1",
    });
    const prepared = session.prepareHistoryItemForModelInput({
      role: "user",
      content: [
        { type: "input_text", text: "Here is my ride." },
        {
          type: "input_file",
          file: { url: "https://files.example/activity.fit" },
          filename: "activity.fit",
        },
      ],
    } as AgentInputItem) as { content: Array<{ type: string; text?: string }> };

    expect(prepared.content).toEqual([
      { type: "input_text", text: "Here is my ride." },
      {
        type: "input_text",
        text:
          "Uploaded file: activity.fit\n" +
          "public_url=https://files.example/activity.fit",
      },
    ]);
  });

  it("rewrites a historical input_file with file.id to text using file_id label", () => {
    const session = new SupabaseAgentSession({
      accessToken: "token",
      baseUrl: "http://localhost",
      leaseId: "lease-1",
    });
    const prepared = session.prepareHistoryItemForModelInput({
      role: "user",
      content: [
        {
          type: "input_file",
          file: { id: "file-abc123" },
          filename: "activity.fit",
        },
      ],
    } as AgentInputItem) as { content: Array<{ type: string; text?: string }> };

    expect(prepared.content).toEqual([
      {
        type: "input_text",
        text: "Uploaded file: activity.fit\nfile_id=file-abc123",
      },
    ]);
  });

  it("rewrites a historical input_file with both url and id to include both labels", () => {
    const session = new SupabaseAgentSession({
      accessToken: "token",
      baseUrl: "http://localhost",
      leaseId: "lease-1",
    });
    const prepared = session.prepareHistoryItemForModelInput({
      role: "user",
      content: [
        {
          type: "input_file",
          file: {
            url: "https://files.example/activity.fit",
            id: "file-abc123",
          },
          filename: "activity.fit",
        },
      ],
    } as AgentInputItem) as { content: Array<{ type: string; text?: string }> };

    expect(prepared.content).toEqual([
      {
        type: "input_text",
        text:
          "Uploaded file: activity.fit\n" +
          "public_url=https://files.example/activity.fit\n" +
          "file_id=file-abc123",
      },
    ]);
  });

  it("keeps historical function calls in the Agents SDK callId shape for model replay", () => {
    const session = new SupabaseAgentSession({
      accessToken: "token",
      baseUrl: "http://localhost",
      leaseId: "lease-1",
      fetch: vi.fn(),
    });

    const prepared = session.prepareHistoryItemForModelInput({
      type: "function_call",
      callId: "call-1",
      name: "update_athlete_profile",
      arguments: "{}",
      status: "completed",
    } as AgentInputItem) as Record<string, unknown>;

    expect(prepared).toMatchObject({
      type: "function_call",
      callId: "call-1",
      name: "update_athlete_profile",
      arguments: "{}",
      status: "completed",
    });
    expect(prepared).not.toHaveProperty("call_id");
  });

  it("keeps historical function call results in the Agents SDK callId shape for model replay", () => {
    const session = new SupabaseAgentSession({
      accessToken: "token",
      baseUrl: "http://localhost",
      leaseId: "lease-1",
      fetch: vi.fn(),
    });

    const prepared = session.prepareHistoryItemForModelInput({
      type: "function_call_result",
      callId: "call-1",
      name: "update_athlete_profile",
      output: JSON.stringify({ status: "pending_implementation" }),
      status: "completed",
    } as AgentInputItem) as Record<string, unknown>;

    expect(prepared).toMatchObject({
      type: "function_call_result",
      callId: "call-1",
      name: "update_athlete_profile",
      output: JSON.stringify({ status: "pending_implementation" }),
      status: "completed",
    });
    expect(prepared).not.toHaveProperty("call_id");
  });

  it("converts raw Responses function_call_output items back to SDK results for model replay", () => {
    const session = new SupabaseAgentSession({
      accessToken: "token",
      baseUrl: "http://localhost",
      leaseId: "lease-1",
      fetch: vi.fn(),
    });
    const output = JSON.stringify({
      error: "Coach is unavailable right now. Please try again.",
    });

    const prepared = session.prepareHistoryItemForModelInput({
      id: "fco-1",
      type: "function_call_output",
      call_id: "call-1",
      output,
      status: "completed",
    } as unknown as AgentInputItem) as Record<string, unknown>;

    expect(prepared).toEqual({
      id: "fco-1",
      type: "function_call_result",
      callId: "call-1",
      name: "call-1",
      output,
      status: "completed",
    });
  });

  it("restores raw Responses tool output content parts to SDK content shapes", () => {
    const session = new SupabaseAgentSession({
      accessToken: "token",
      baseUrl: "http://localhost",
      leaseId: "lease-1",
      fetch: vi.fn(),
    });

    const prepared = session.prepareHistoryItemForModelInput({
      type: "function_call_output",
      call_id: "call-1",
      function_name: "get_weather",
      created_by: "model",
      output: [
        { type: "input_text", text: "18C, clear skies." },
        {
          type: "input_image",
          image_url: "https://files.example/weather.png",
          detail: "high",
        },
        {
          type: "input_file",
          file_id: "file-abc123",
          filename: "weather.txt",
        },
      ],
    } as unknown as AgentInputItem) as FunctionCallResultItem;

    expect(prepared).toEqual({
      type: "function_call_result",
      callId: "call-1",
      name: "get_weather",
      output: [
        { type: "input_text", text: "18C, clear skies." },
        {
          type: "input_image",
          image: "https://files.example/weather.png",
          detail: "high",
        },
        {
          type: "input_file",
          file: { id: "file-abc123" },
          filename: "weather.txt",
        },
      ],
      providerData: { created_by: "model" },
      status: "completed",
    });

    const model = new InspectableResponsesModel({} as never, "test-model");
    expect(model.buildInput([prepared])).toEqual([
      {
        type: "function_call_output",
        id: undefined,
        call_id: "call-1",
        created_by: "model",
        output: [
          { type: "input_text", text: "18C, clear skies." },
          {
            type: "input_image",
            image_url: "https://files.example/weather.png",
            detail: "high",
          },
          {
            type: "input_file",
            file_id: "file-abc123",
            filename: "weather.txt",
          },
        ],
        status: "completed",
      },
    ]);
  });

  // OpenAI may retain a raw tool pair in compacted.output even though it does
  // not guarantee that any particular pair survives. Our stored canonical
  // window therefore needs a deterministic raw→SDK adapter for the conditional
  // case. A live compaction probe cannot force this output shape; this fixture
  // can, and then exercises the locked SDK's real request conversion.
  it("converts a synthetic retained raw tool pair into replayable SDK input", () => {
    const session = new SupabaseAgentSession({
      accessToken: "token",
      baseUrl: "http://localhost",
      leaseId: "lease-1",
      fetch: vi.fn(),
    });
    const rawItems = [
      {
        type: "function_call",
        call_id: "call-1",
        name: "update_athlete_profile",
        arguments: "{}",
        status: "completed",
      },
      {
        type: "function_call_output",
        call_id: "call-1",
        output: JSON.stringify({ status: "updated" }),
        status: "completed",
      },
    ] as unknown as AgentInputItem[];
    const model = new InspectableResponsesModel({} as never, "test-model");

    expect(() => model.buildInput([rawItems[1] as AgentInputItem])).toThrow(
      /Unsupported item/,
    );

    const prepared = rawItems.map((item) =>
      session.prepareHistoryItemForModelInput(item),
    );
    const requestInput = model.buildInput(prepared) as Record<
      string,
      unknown
    >[];
    expect(requestInput).toHaveLength(2);
    expect(requestInput[0]).toEqual(
      expect.objectContaining({
        type: "function_call",
        call_id: "call-1",
        name: "update_athlete_profile",
        arguments: "{}",
        status: "completed",
      }),
    );
    expect(requestInput[1]).toEqual(
      expect.objectContaining({
        type: "function_call_output",
        call_id: "call-1",
        output: JSON.stringify({ status: "updated" }),
        status: "completed",
      }),
    );
  });

  // Same argument for the structured-output array: the legacy `text`/`image`/
  // `file` discriminators this converter replaced are not merely a different
  // spelling the SDK tolerates, they are a hard rejection. That is what makes
  // the mapping assertion above load-bearing rather than decorative.
  it("rejects the legacy structured tool output discriminators it replaced", () => {
    const model = new InspectableResponsesModel({} as never, "test-model");
    const legacy = {
      type: "function_call_result",
      callId: "call-1",
      name: "get_weather",
      status: "completed",
      output: [{ type: "text", text: "18C, clear skies." }],
    } as unknown as AgentInputItem;

    expect(() => model.buildInput([legacy])).toThrow(
      /Unsupported structured tool output/,
    );
  });

  it("degrades a malformed raw tool output without a call ID to replayable text", () => {
    const session = new SupabaseAgentSession({
      accessToken: "token",
      baseUrl: "http://localhost",
      leaseId: "lease-1",
      fetch: vi.fn(),
    });

    const prepared = session.prepareHistoryItemForModelInput({
      type: "function_call_output",
      output: "orphaned",
    } as unknown as AgentInputItem);

    // Shape is the contract; the wording is athlete-visible copy and is free to
    // change without breaking replay, so it is matched as "some non-empty text"
    // rather than pinned verbatim.
    expect(prepared).toEqual({
      role: "assistant",
      status: "completed",
      content: [{ type: "output_text", text: expect.stringMatching(/\S/) }],
    });

    // The degraded item is built behind a bare `as AgentInputItem` cast, so the
    // type system is silenced on exactly the shape that has never been checked
    // against request conversion — and this branch fires on already-malformed
    // history, the case most likely to be live. Assert the fallback is itself
    // replayable rather than assuming it.
    const model = new InspectableResponsesModel({} as never, "test-model");
    expect(model.buildInput([prepared])).toEqual([
      {
        type: "message",
        role: "assistant",
        status: "completed",
        content: [
          {
            type: "output_text",
            annotations: [],
            text: expect.stringMatching(/\S/),
          },
        ],
      },
    ]);
  });

  // `tests/integration/oai-agents.test.ts` instantiates this class purely to run
  // compacted history through the *production* sanitizer, and constructs it with
  // placeholder credentials and no injected `fetch`. That is only sound while
  // `prepareHistoryItemForModelInput` stays free of I/O — it must not read
  // `options`, call the backend, or touch `/api/chat/model-state`. Despite the
  // class name there is no Supabase SDK anywhere in this module's import graph;
  // it is an HTTP client against the app's own route, and this method delegates
  // to the pure helpers in `responses-item-shapes.ts`. If someone later makes the
  // method fetch (say, to resolve an attachment link), the integration suite would
  // start reaching the network with a bogus base URL instead of failing here.
  it("prepares history items for model input without performing any I/O", () => {
    const fetchSpy = vi.fn(() => {
      throw new Error("prepareHistoryItemForModelInput must not perform I/O");
    });
    const session = new SupabaseAgentSession({
      accessToken: "token",
      baseUrl: "http://localhost",
      leaseId: "lease-1",
      fetch: fetchSpy as unknown as typeof fetch,
    });

    const items: AgentInputItem[] = [
      userItem("plain text"),
      {
        role: "user",
        content: [
          { type: "input_image", image: "https://files.example/photo.png" },
          {
            type: "input_file",
            file: { url: "https://files.example/ride.fit" },
          },
        ],
      } as unknown as AgentInputItem,
      {
        type: "function_call_output",
        call_id: "call-1",
        output: "done",
      } as unknown as AgentInputItem,
      {
        type: "function_call_output",
        output: "orphaned",
      } as unknown as AgentInputItem,
    ];

    for (const item of items) {
      expect(() => session.prepareHistoryItemForModelInput(item)).not.toThrow();
    }
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
