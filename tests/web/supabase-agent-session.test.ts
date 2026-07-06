import type { AgentInputItem } from "@openai/agents";
import { describe, expect, it, vi } from "vitest";

import { SupabaseAgentSession } from "../../lib/agent/supabase-agent-session";

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
