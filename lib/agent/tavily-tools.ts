import { createMCPClient } from "@ai-sdk/mcp";
import type { ToolSet } from "ai";

import { buildTavilyMcpUrl } from "../site";

type TavilyClient = {
  close: () => Promise<void>;
  tools: () => Promise<ToolSet>;
};

type TavilyClientFactory = (config: {
  transport: { type: "http"; url: string };
}) => Promise<TavilyClient>;

type TavilyToolProviderOptions = {
  buildUrl?: (apiKey: string) => string;
  createClient?: TavilyClientFactory;
};

export type TavilyToolProvider = {
  close: () => Promise<void>;
  getTools: (apiKey: string | undefined) => Promise<ToolSet>;
};

type ActiveTavilyClient = {
  apiKey: string;
  clientPromise: Promise<TavilyClient>;
  toolsPromise: Promise<ToolSet>;
};

export function createTavilyToolProvider({
  buildUrl = buildTavilyMcpUrl,
  createClient = (config): Promise<TavilyClient> => createMCPClient(config),
}: TavilyToolProviderOptions = {}): TavilyToolProvider {
  let active: ActiveTavilyClient | null = null;

  async function close(): Promise<void> {
    const current = active;
    active = null;
    if (current === null) return;

    try {
      const client = await current.clientPromise;
      await client.close();
    } catch {
      // A failed initialization has no usable client to close.
    }
  }

  async function getTools(apiKey: string | undefined): Promise<ToolSet> {
    if (!apiKey) return {};
    if (active?.apiKey === apiKey) return active.toolsPromise;
    if (active !== null) await close();

    const clientPromise = createClient({
      transport: { type: "http", url: buildUrl(apiKey) },
    });
    const toolsPromise = clientPromise.then((client) => client.tools());
    const next: ActiveTavilyClient = { apiKey, clientPromise, toolsPromise };
    active = next;

    try {
      return await toolsPromise;
    } catch (error) {
      if (active === next) active = null;
      try {
        const client = await clientPromise;
        await client.close();
      } catch {
        // Initialization failures may not yield a client.
      }
      throw error;
    }
  }

  return { close, getTools };
}
