import type { ToolSet } from "ai";
import { describe, expect, it, vi } from "vitest";

import { createTavilyToolProvider } from "../../lib/agent/tavily-tools";

type TestClient = {
  close: () => Promise<void>;
  tools: () => Promise<ToolSet>;
};

function client(tools: ToolSet = {}): TestClient {
  return {
    close: vi.fn(() => Promise.resolve()),
    tools: vi.fn(() => Promise.resolve(tools)),
  };
}

describe("createTavilyToolProvider", () => {
  it("returns no tools without initializing when the API key is absent", async () => {
    const createClient = vi.fn();
    const provider = createTavilyToolProvider({
      createClient: createClient as never,
    });

    await expect(provider.getTools(undefined)).resolves.toEqual({});
    expect(createClient).not.toHaveBeenCalled();
  });

  it("shares tool discovery across concurrent and later callers", async () => {
    const tools = { search: { description: "search" } } as unknown as ToolSet;
    const tavilyClient = client(tools);
    const createClient = vi.fn(() => Promise.resolve(tavilyClient));
    const provider = createTavilyToolProvider({
      createClient: createClient as never,
    });

    const first = provider.getTools("secret-key");
    const second = provider.getTools("secret-key");

    await expect(Promise.all([first, second])).resolves.toEqual([tools, tools]);
    await expect(provider.getTools("secret-key")).resolves.toBe(tools);
    expect(createClient).toHaveBeenCalledTimes(1);
    expect(tavilyClient.tools).toHaveBeenCalledTimes(1);
  });

  it("clears a failed initialization so a later request can retry", async () => {
    const tools = { search: { description: "search" } } as unknown as ToolSet;
    const createClient = vi
      .fn()
      .mockRejectedValueOnce(new Error("MCP unavailable"))
      .mockResolvedValueOnce(client(tools));
    const provider = createTavilyToolProvider({
      createClient: createClient as never,
    });

    await expect(provider.getTools("secret-key")).rejects.toThrow(
      "MCP unavailable",
    );
    await expect(provider.getTools("secret-key")).resolves.toBe(tools);
    expect(createClient).toHaveBeenCalledTimes(2);
  });

  it("closes the active client", async () => {
    const tavilyClient = client();
    const provider = createTavilyToolProvider({
      createClient: vi.fn(() => Promise.resolve(tavilyClient)) as never,
    });
    await provider.getTools("secret-key");

    await provider.close();

    expect(tavilyClient.close).toHaveBeenCalledOnce();
  });
});
