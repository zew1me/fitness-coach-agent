import { createMCPClient } from "@ai-sdk/mcp";
import { openai } from "@ai-sdk/openai";
import { convertToModelMessages, streamText, type ToolSet, type UIMessage } from "ai";

import { createCoachTools } from "../../../lib/agent/coach-tools";
import { selectMessagesForModel } from "../../../lib/agent/message-context";
import { buildCoachSystemPrompt } from "../../../lib/agent/system-prompt";
import type { AthleteContextBundle } from "../../../lib/agent/types";
import { buildTavilyMcpUrl } from "../../../lib/site";

export const runtime = "nodejs";

type BrowserTokenResponse = {
  access_token: string;
  user_id: string;
};

type ChatRequestBody = {
  messages?: UIMessage[];
};

const AUTH_UNAVAILABLE_MESSAGE =
  "Something went wrong. Please refresh and try again.";

function jsonError(message: string, status: number): Response {
  return Response.json({ error: message }, { status });
}

function requestOrigin(request: Request): string {
  const url = new URL(request.url);
  return `${url.protocol}//${url.host}`;
}

async function loadBrowserToken(request: Request): Promise<BrowserTokenResponse | null> {
  const cookie = request.headers.get("cookie");
  if (!cookie?.includes("coach_browser_session=")) {
    return null;
  }

  const response = await fetch(`${requestOrigin(request)}/api/oauth/browser-token`, {
    method: "POST",
    headers: { cookie }
  });
  if (!response.ok) {
    return null;
  }
  return (await response.json()) as BrowserTokenResponse;
}

async function loadAthleteContext(
  request: Request,
  token: BrowserTokenResponse
): Promise<AthleteContextBundle> {
  const response = await fetch(`${requestOrigin(request)}/api/engine/get-athlete-summary`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token.access_token}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({})
  });

  if (!response.ok) {
    throw new Error("Unable to load athlete context.");
  }

  return (await response.json()) as AthleteContextBundle;
}

export async function POST(request: Request): Promise<Response> {
  let token: BrowserTokenResponse | null;
  try {
    token = await loadBrowserToken(request);
  } catch {
    return jsonError(AUTH_UNAVAILABLE_MESSAGE, 503);
  }

  if (token === null) {
    return jsonError("Missing browser session cookie.", 401);
  }

  const UNAVAILABLE = "Coach is unavailable right now. Please try again.";

  try {
    const body = (await request.json()) as ChatRequestBody;
    const messages = body.messages ?? [];
    const modelMessages = selectMessagesForModel(messages);
    const context = await loadAthleteContext(request, token);

    const tavilyApiKey = process.env["TAVILY_API_KEY"];
    const tavilyTools: ToolSet = tavilyApiKey
      ? await createMCPClient({
          transport: { type: "http", url: buildTavilyMcpUrl(tavilyApiKey) },
        }).then((c) => c.tools())
      : {};

    const result = streamText({
      model: openai("gpt-5-mini"),
      system: buildCoachSystemPrompt(context),
      messages: await convertToModelMessages(modelMessages),
      tools: {
        ...createCoachTools({
          accessToken: token.access_token,
          baseUrl: requestOrigin(request),
        }),
        ...tavilyTools,
      },
      onError: ({ error }) => {
        const msg = error instanceof Error ? error.message : String(error);
        console.error("[chat] stream error:", msg.replace(/key=[^&\s]+/g, "key=***"));
      },
    });

    return result.toUIMessageStreamResponse({
      onError: () => UNAVAILABLE,
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    console.error("[chat] POST error:", msg.replace(/key=[^&\s]+/g, "key=***"));
    return new Response(UNAVAILABLE, { status: 503 });
  }
}
