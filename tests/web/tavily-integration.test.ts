/**
 * Tavily MCP integration tests — exercise the real HTTP transport.
 *
 * These tests are skipped when TAVILY_API_KEY is absent so they never block
 * CI environments that don't have the key.  Run them locally with:
 *
 *   TAVILY_API_KEY=tvly-... bun run test --reporter=verbose tests/web/tavily-integration.test.ts
 */
import { describe, expect, it } from "vitest";

import { createTavilyToolProvider } from "../../lib/agent/tavily-tools";

const TAVILY_API_KEY = process.env["TAVILY_API_KEY"];

describe.skipIf(!TAVILY_API_KEY)("Tavily MCP integration", () => {
  it("connects and returns at least one search tool", async () => {
    const provider = createTavilyToolProvider();
    try {
      const tools = await provider.getTools(TAVILY_API_KEY);
      expect(Object.keys(tools).length).toBeGreaterThan(0);
      expect(
        Object.keys(tools).some((name) =>
          name.toLowerCase().includes("search"),
        ),
      ).toBe(true);
    } finally {
      await provider.close();
    }
  });

  it("caches the connection across repeated getTools calls", async () => {
    const provider = createTavilyToolProvider();
    try {
      const first = await provider.getTools(TAVILY_API_KEY);
      const second = await provider.getTools(TAVILY_API_KEY);
      // Same object reference confirms no second MCP handshake occurred
      expect(second).toBe(first);
    } finally {
      await provider.close();
    }
  });

  it("executes a web search and returns non-empty results", async () => {
    const provider = createTavilyToolProvider();
    try {
      const tools = await provider.getTools(TAVILY_API_KEY);
      const searchTool = Object.entries(tools).find(([name]) =>
        name.toLowerCase().includes("search"),
      );
      expect(searchTool).toBeDefined();
      const [, tool] = searchTool!;

      const result = await (
        tool as { execute: (args: Record<string, unknown>) => Promise<unknown> }
      ).execute({ query: "upcoming marathons Indianapolis 2025 2026" });

      const text = typeof result === "string" ? result : JSON.stringify(result);
      expect(text.length).toBeGreaterThan(50);
    } finally {
      await provider.close();
    }
  });
});
