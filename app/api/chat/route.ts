import * as Sentry from "@sentry/nextjs";
import { type UIMessage } from "ai";

import {
  appendImageExtractionsToMessages,
  convertUnsupportedFilePartsToText,
  selectMessagesForModel,
} from "../../../lib/agent/message-context";
import { streamCoachTurn } from "../../../lib/agent/orchestrator";
import { createTavilyToolProvider } from "../../../lib/agent/tavily-tools";
import type { AthleteContextBundle } from "../../../lib/agent/types";
import { chatRequestBodySchema } from "../../../lib/schemas";

export const runtime = "nodejs";

type BrowserTokenResponse = {
  access_token: string;
  user_id: string;
};

type LatestUserTurn = {
  id: string;
  parts: UIMessage["parts"];
};

const AUTH_UNAVAILABLE_MESSAGE =
  "Something went wrong. Please refresh and try again.";

const BROWSER_TOKEN_TIMEOUT_MS = 5_000;
const ATHLETE_CONTEXT_TIMEOUT_MS = 20_000;
const PERSIST_MESSAGE_TIMEOUT_MS = 10_000;
const SCREENSHOT_ANALYSIS_TIMEOUT_MS = 90_000;
const COACH_UNAVAILABLE_MESSAGE =
  "Coach is out to lunch. Please try again later.";
const tavilyToolProvider = createTavilyToolProvider();

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
      signal: AbortSignal.timeout(BROWSER_TOKEN_TIMEOUT_MS),
    },
  );
  if (!response.ok) {
    Sentry.logger.error("chat: browser token fetch failed", {
      status: response.status,
    });
    throw new Error(
      `Browser token fetch failed with status ${response.status}`,
    );
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
      signal: AbortSignal.timeout(ATHLETE_CONTEXT_TIMEOUT_MS),
    },
  );

  if (!response.ok) {
    Sentry.logger.error("chat: athlete context load failed", {
      status: response.status,
    });
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
        signal: AbortSignal.timeout(PERSIST_MESSAGE_TIMEOUT_MS),
      },
    );
    if (!response.ok) {
      Sentry.logger.warn("chat: persist user message failed", {
        status: response.status,
      });
    }
  } catch (error) {
    Sentry.logger.error("chat: persist user message error", {
      error: safeErrorMessage(error),
    });
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
        signal: AbortSignal.timeout(SCREENSHOT_ANALYSIS_TIMEOUT_MS),
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
  } catch (error) {
    Sentry.logger.warn("chat: screenshot extraction failed", {
      error: safeErrorMessage(error),
    });
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
    Sentry.logger.warn("chat: missing browser session cookie");
    return jsonError("Missing browser session cookie.", 401);
  }

  try {
    return await streamCoachTurnWithContext(request, token);
  } catch (error) {
    Sentry.logger.error("chat: POST error", { error: safeErrorMessage(error) });
    return new Response(COACH_UNAVAILABLE_MESSAGE, { status: 503 });
  }
}

async function streamCoachTurnWithContext(
  request: Request,
  token: BrowserTokenResponse,
): Promise<Response> {
  let requestBody: unknown;
  try {
    requestBody = await request.json();
  } catch {
    return jsonError("Invalid request body.", 400);
  }
  const parseResult = chatRequestBodySchema.safeParse(requestBody);
  if (!parseResult.success) {
    return jsonError("Invalid request body.", 400);
  }
  const messages = (parseResult.data.messages ?? []) as unknown as UIMessage[];
  Sentry.logger.info("chat turn start", {
    message_count: messages.length,
  });
  const modelMessages = await appendImageExtractionsToMessages(
    convertUnsupportedFilePartsToText(selectMessagesForModel(messages)),
    ({ imageUrl }) => extractImageContent(request, token, imageUrl),
  );
  const latestUserTurn = summarizeLatestUserTurn(messages);
  if (latestUserTurn !== null) {
    await persistUserMessage(request, token, latestUserTurn);
  }
  const context = await loadAthleteContext(request, token);

  const tavilyTools = await tavilyToolProvider.getTools(
    process.env["TAVILY_API_KEY"],
  );
  Sentry.logger.info("chat: tavily tools loaded", {
    count: Object.keys(tavilyTools).length,
  });

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
}
