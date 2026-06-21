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
