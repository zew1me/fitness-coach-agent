import { createMCPClient } from "@ai-sdk/mcp";
import { type ToolSet, type UIMessage } from "ai";

import {
  appendImageExtractionsToMessages,
  convertUnsupportedFilePartsToText,
  selectMessagesForModel,
} from "../../../lib/agent/message-context";
import { streamCoachTurn } from "../../../lib/agent/orchestrator";
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

type LatestUserTurn = {
  id: string;
  parts: UIMessage["parts"];
};

const AUTH_UNAVAILABLE_MESSAGE =
  "Something went wrong. Please refresh and try again.";
const COACH_UNAVAILABLE_MESSAGE =
  "Coach is unavailable right now. Please try again.";

function jsonError(message: string, status: number): Response {
  return Response.json({ error: message }, { status });
}

function requestOrigin(request: Request): string {
  const url = new URL(request.url);
  return `${url.protocol}//${url.host}`;
}

function vercelProtectionBypassHeaders(): Record<string, string> {
  const bypassSecret = process.env["VERCEL_AUTOMATION_BYPASS_SECRET"];
  return bypassSecret ? { "x-vercel-protection-bypass": bypassSecret } : {};
}

function safeErrorMessage(error: unknown): string {
  const msg = error instanceof Error ? error.message : String(error);
  return msg.replace(/key=[^&\s]+/g, "key=***");
}

async function loadBrowserToken(
  request: Request,
): Promise<BrowserTokenResponse | null> {
  const cookie = request.headers.get("cookie");
  if (!cookie?.includes("coach_browser_session=")) {
    return null;
  }

  const response = await fetch(
    `${requestOrigin(request)}/api/oauth/browser-token`,
    {
      method: "POST",
      headers: { cookie, ...vercelProtectionBypassHeaders() },
    },
  );
  if (!response.ok) {
    return null;
  }
  return (await response.json()) as BrowserTokenResponse;
}

async function loadAthleteContext(
  request: Request,
  token: BrowserTokenResponse,
): Promise<AthleteContextBundle> {
  const response = await fetch(
    `${requestOrigin(request)}/api/engine/get-athlete-summary`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token.access_token}`,
        "Content-Type": "application/json",
        ...vercelProtectionBypassHeaders(),
      },
      body: JSON.stringify({}),
    },
  );

  if (!response.ok) {
    throw new Error("Unable to load athlete context.");
  }

  return (await response.json()) as AthleteContextBundle;
}

function summarizeLatestUserTurn(messages: UIMessage[]): LatestUserTurn | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i];
    if (message?.role !== "user") continue;
    return {
      id: message.id,
      parts: message.parts,
    };
  }
  return null;
}

async function persistUserMessage(
  request: Request,
  token: BrowserTokenResponse,
  turn: LatestUserTurn,
): Promise<void> {
  if (turn.parts.length === 0) return;
  try {
    const response = await fetch(
      `${requestOrigin(request)}/api/chat/messages`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token.access_token}`,
          "Content-Type": "application/json",
          ...vercelProtectionBypassHeaders(),
        },
        body: JSON.stringify({
          id: turn.id,
          role: "user",
          parts: turn.parts,
          metadata: { message_kind: "user_turn", client_message_id: turn.id },
        }),
      },
    );
    if (!response.ok) {
      console.error("[chat] persist user message failed:", response.status);
    }
  } catch (error) {
    console.error(
      "[chat] persist user message error:",
      safeErrorMessage(error),
    );
  }
}

async function extractImageContent(
  request: Request,
  token: BrowserTokenResponse,
  imageUrl: string,
): Promise<{ data: unknown; screenshot_type: string } | null> {
  try {
    const response = await fetch(
      `${requestOrigin(request)}/api/engine/analyze-screenshot`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token.access_token}`,
          "Content-Type": "application/json",
          ...vercelProtectionBypassHeaders(),
        },
        body: JSON.stringify({ image_url: imageUrl }),
      },
    );

    if (!response.ok) {
      return null;
    }

    const payload = (await response.json()) as {
      data?: unknown;
      screenshot_type?: unknown;
    };
    return {
      data: payload.data ?? {},
      screenshot_type:
        typeof payload.screenshot_type === "string"
          ? payload.screenshot_type
          : "unknown",
    };
  } catch {
    return null;
  }
}

export async function POST(request: Request): Promise<Response> {
  let token: BrowserTokenResponse | null;
  try {
    token = await loadBrowserToken(request);
  } catch (error) {
    console.error("[chat] loadBrowserToken failed", {
      message: error instanceof Error ? error.message : String(error),
      stack: error instanceof Error ? error.stack : undefined,
    });
    return jsonError(AUTH_UNAVAILABLE_MESSAGE, 503);
  }

  if (token === null) {
    return jsonError("Missing browser session cookie.", 401);
  }

  try {
    const body = (await request.json()) as ChatRequestBody;
    const messages = body.messages ?? [];
    const modelMessages = await appendImageExtractionsToMessages(
      convertUnsupportedFilePartsToText(selectMessagesForModel(messages)),
      ({ imageUrl }) => extractImageContent(request, token, imageUrl),
    );
    const latestUserTurn = summarizeLatestUserTurn(messages);
    if (latestUserTurn !== null) {
      await persistUserMessage(request, token, latestUserTurn);
    }
    const context = await loadAthleteContext(request, token);

    const tavilyApiKey = process.env["TAVILY_API_KEY"];
    const tavilyTools: ToolSet = tavilyApiKey
      ? await createMCPClient({
          transport: { type: "http", url: buildTavilyMcpUrl(tavilyApiKey) },
        }).then((c) => c.tools())
      : {};

    return await streamCoachTurn({
      accessToken: token.access_token,
      baseUrl: requestOrigin(request),
      context,
      extraHeaders: vercelProtectionBypassHeaders(),
      messages: modelMessages,
      messagesAreModelSelected: true,
      streamErrorMessage: COACH_UNAVAILABLE_MESSAGE,
      tavilyTools,
    });
  } catch (error) {
    console.error("[chat] POST error:", safeErrorMessage(error));
    return new Response(COACH_UNAVAILABLE_MESSAGE, { status: 503 });
  }
}
