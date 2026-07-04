export const siteConfig = {
  appName: "Exercise Training Plan GPT",
  description: "Endurance sport coaching app with durable athlete profiles and adaptive plans."
} as const;

const TAVILY_MCP_BASE_URL = "https://mcp.tavily.com/mcp/";

export function buildTavilyMcpUrl(apiKey: string): string {
  const url = new URL(TAVILY_MCP_BASE_URL);
  url.searchParams.set("tavilyApiKey", apiKey);
  return url.toString();
}
