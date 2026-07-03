import type { AgentInputItem } from "@openai/agents";
import { describe, expect, it, vi } from "vitest";

import {
  DurableCompactionSession,
  SupabaseAgentSession,
  estimateStoredContext,
} from "../../lib/agent/supabase-agent-session";

const userItem = (text: string): AgentInputItem => ({
  role: "user",
  content: [{ type: "input_text", text }],
});

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

  it("rewrites raw Responses function_call_output items to assistant text before model replay", () => {
    const session = new SupabaseAgentSession({
      accessToken: "token",
      baseUrl: "http://localhost",
      leaseId: "lease-1",
      fetch: vi.fn(),
    });

    const prepared = session.prepareHistoryItemForModelInput({
      type: "function_call_output",
      call_id: "call-1",
      output: JSON.stringify({
        error: "Coach is unavailable right now. Please try again.",
      }),
      status: "completed",
    } as unknown as AgentInputItem) as Record<string, unknown>;

    expect(prepared).toEqual({
      role: "assistant",
      status: "completed",
      content: [
        {
          type: "output_text",
          text:
            "Historical tool output omitted from model replay. " +
            "The visible chat transcript is preserved separately.",
        },
      ],
    });
  });
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
        model: "gpt-5.4-mini",
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

    expect(client.responses.compact).toHaveBeenCalledWith(
      expect.objectContaining({
        previous_response_id: "resp_123",
      }),
    );
    const request = client.responses.compact.mock.calls[0]?.[0];
    expect(request).not.toHaveProperty("force");
    expect(request).not.toHaveProperty("compactionMode");
    expect(request).not.toHaveProperty("responseId");
    expect(request).not.toHaveProperty("store");
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
