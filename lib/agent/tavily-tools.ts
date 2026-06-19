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
  let initializationLock: Promise<void> | null = null;

  async function close(): Promise<void> {
    const current = active;
    active = null;
    if (current === null) return;

    try {
      const client = await current.clientPromise;
      await client.close();
    } catch (error) {
      // clientPromise may have rejected (no client to close), or close() itself
      // threw (connection already gone). Either way the resource is released.
      console.warn(
        "[tavily] client close error:",
        error instanceof Error ? error.message : String(error),
      );
    }
  }

  // Close until active is null or matches apiKey — guards against a concurrent
  // call installing a new client during the await inside close().
  async function closeUntilKeyMatches(apiKey: string): Promise<void> {
    if (active !== null && active.apiKey !== apiKey) {
      await close();
    }
  }

  async function acquireLockIfNeeded(
    apiKey: string,
  ): Promise<(() => void) | ToolSet> {
    while (initializationLock !== null) {
      await initializationLock;
      if (active?.apiKey === apiKey) return active.toolsPromise;
    }

    let releaseLock = (): void => {};
    initializationLock = new Promise<void>((resolve) => {
      releaseLock = resolve;
    });
    return releaseLock;
  }

  async function initializeTools(apiKey: string): Promise<ToolSet> {
    await closeUntilKeyMatches(apiKey);
    if (active?.apiKey === apiKey) return active.toolsPromise;

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

  async function getTools(apiKey: string | undefined): Promise<ToolSet> {
    if (!apiKey) return {};
    if (active?.apiKey === apiKey) return active.toolsPromise;

    const lockOrTools = await acquireLockIfNeeded(apiKey);
    if (typeof lockOrTools !== "function") return lockOrTools;
    const releaseLock = lockOrTools;

    try {
      return await initializeTools(apiKey);
    } finally {
      initializationLock = null;
      releaseLock();
    }
  }

  return { close, getTools };
}
