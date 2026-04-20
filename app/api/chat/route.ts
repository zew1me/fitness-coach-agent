import { createMCPClient } from "@ai-sdk/mcp";
import { openai } from "@ai-sdk/openai";
import { convertToModelMessages, streamText, type ToolSet, type UIMessage } from "ai";

import { createCoachTools } from "../../../lib/agent/coach-tools";
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

const MODEL_RECENT_MESSAGE_LIMIT = 24;

function jsonError(message: string, status: number): Response {
  return Response.json({ error: message }, { status });
}

export function selectMessagesForModel(messages: UIMessage[]): UIMessage[] {
  if (messages.length <= MODEL_RECENT_MESSAGE_LIMIT) {
    return messages;
  }

  const omittedCount = messages.length - MODEL_RECENT_MESSAGE_LIMIT;
  return [
    {
      id: "context-window-notice",
      parts: [
        {
          type: "text",
          text:
            `The previous ${omittedCount} chat messages are persisted in the coaching history ` +
            "but omitted from this model turn to keep context focused. Continue from the recent " +
            "messages and use athlete data tools when older details are needed.",
        },
      ],
      role: "system",
    },
    ...messages.slice(-MODEL_RECENT_MESSAGE_LIMIT),
  ];
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
    body: JSON.stringify({ user_id: token.user_id })
  });

  if (!response.ok) {
    throw new Error("Unable to load athlete context.");
  }

  return (await response.json()) as AthleteContextBundle;
}

export async function POST(request: Request): Promise<Response> {
  const token = await loadBrowserToken(request);
  if (token === null) {
    return jsonError("Missing browser session cookie.", 401);
  }

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
    model: openai("gpt-4.1-mini"),
    system: buildCoachSystemPrompt(context),
    messages: await convertToModelMessages(modelMessages),
    tools: {
      ...createCoachTools({
        accessToken: token.access_token,
        baseUrl: requestOrigin(request),
        userId: token.user_id,
      }),
      ...tavilyTools,
    },
  });

  return result.toUIMessageStreamResponse();
}
