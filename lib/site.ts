export const siteConfig = {
  appName: "Coach Arden",
  description:
    "AI Chat Bot for endurance athletes that helps plan, adapt, and review training using athlete profile and workout data.",
} as const;

const TAVILY_MCP_BASE_URL = "https://mcp.tavily.com/mcp/";

export function buildTavilyMcpUrl(apiKey: string): string {
  const url = new URL(TAVILY_MCP_BASE_URL);
  url.searchParams.set("tavilyApiKey", apiKey);
  return url.toString();
}
