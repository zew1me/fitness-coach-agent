import {
  Agent,
  MCPServerStreamableHttp,
  Runner,
  withTrace,
  type MCPServer,
} from "@openai/agents";
import {
  createUIMessageStream,
  createUIMessageStreamResponse,
  type UIMessage,
} from "ai";

import { toAgentInputItems } from "./agent-input";
import { finishAgentText, writeAgentStreamEvent } from "./agent-stream";
import {
  createAgentCoachTools,
  type CoachAgentRunContext,
} from "./coach-tools";
import { buildContextSlices } from "./context-slices";
import { routeTurnIntent } from "./intent-router";
import { selectMessagesForModel } from "./message-context";
import {
  specialistReportsSchema,
  type SpecialistReport,
} from "./orchestration-types";
import { runSpecialists } from "./specialists";
import { buildLeadCoachPrompt } from "./system-prompt";
import type { AthleteContextBundle } from "./types";

export type StreamCoachTurnOptions = {
  accessToken: string;
  baseUrl: string;
  context: AthleteContextBundle;
  extraHeaders?: Record<string, string>;
  messages: UIMessage[];
  messagesAreModelSelected?: boolean;
  signal?: AbortSignal;
  streamErrorMessage?: string;
  tavilyMcpUrl?: string;
};

const MAX_COACH_STEPS = 4;
const MODEL = "gpt-5-mini-2025-08-07";

function latestUserText(messages: UIMessage[]): string {
  const latest = [...messages]
    .reverse()
    .find((message) => message.role === "user");
  if (!latest) return "";

  return latest.parts
    .map((part) => (part.type === "text" ? part.text : ""))
    .filter(Boolean)
    .join("\n");
}

function generateUuid(): string {
  return crypto.randomUUID();
}

async function persistAssistantMessage(
  options: Pick<
    StreamCoachTurnOptions,
    "accessToken" | "baseUrl" | "extraHeaders"
  >,
  responseMessage: UIMessage,
  finishReason: string | undefined,
): Promise<void> {
  if (responseMessage.parts.length === 0) return;
  try {
    const response = await fetch(`${options.baseUrl}/api/chat/messages`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${options.accessToken}`,
        "Content-Type": "application/json",
        ...(options.extraHeaders ?? {}),
      },
      body: JSON.stringify({
        id: responseMessage.id,
        role: "assistant",
        parts: responseMessage.parts,
        metadata: {
          message_kind: "assistant_reply",
          finish_reason: finishReason,
          client_message_id: responseMessage.id,
        },
      }),
    });
    if (!response.ok) {
      console.error("[chat] persist assistant reply failed:", response.status);
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error("[chat] persist assistant reply error:", message);
  }
}

function createTavilyServer(url: string | undefined): MCPServer | null {
  if (url === undefined) return null;
  return new MCPServerStreamableHttp({
    cacheToolsList: true,
    name: "tavily",
    url,
  });
}

export function streamCoachTurn({
  accessToken,
  baseUrl,
  context,
  extraHeaders,
  messages,
  messagesAreModelSelected = false,
  signal,
  streamErrorMessage = "Coach is unavailable right now. Please try again.",
  tavilyMcpUrl,
}: StreamCoachTurnOptions): Response {
  const selectedMessages = messagesAreModelSelected
    ? messages
    : selectMessagesForModel(messages);
  const runContext: CoachAgentRunContext = { toolCalled: false };

  const stream = createUIMessageStream<UIMessage>({
    generateId: generateUuid,
    originalMessages: selectedMessages,
    onError: () => streamErrorMessage,
    onFinish: async ({ finishReason, isAborted, responseMessage }) => {
      if (isAborted) return;
      await persistAssistantMessage(
        {
          accessToken,
          baseUrl,
          ...(extraHeaders ? { extraHeaders } : {}),
        },
        responseMessage,
        finishReason,
      );
    },
    execute: async ({ writer }) => {
      writer.write({ type: "start" });
      writer.write({ type: "start-step" });
      const textState = { textId: "coach-response", textStarted: false };

      try {
        await withTrace(
          "fitness-coach-turn",
          async () => {
            const intent = routeTurnIntent(
              latestUserText(selectedMessages),
              context,
            );
            const reports = specialistReportsSchema.parse(
              await runSpecialists({
                messages: selectedMessages,
                messagesAreModelSelected: true,
                model: MODEL,
                roles: intent.specialists,
                slices: buildContextSlices(context),
              }),
            ) as SpecialistReport[];
            const tavilyServer = createTavilyServer(tavilyMcpUrl);
            if (tavilyServer !== null) await tavilyServer.connect();

            try {
              const lead = new Agent<CoachAgentRunContext>({
                name: "Lead coach",
                instructions: buildLeadCoachPrompt(context, reports),
                model: MODEL,
                mcpServers: tavilyServer === null ? [] : [tavilyServer],
                tools: createAgentCoachTools({
                  accessToken,
                  baseUrl,
                  ...(extraHeaders ? { extraHeaders } : {}),
                }),
              });
              lead.on("agent_tool_start", (activeContext) => {
                activeContext.context.toolCalled = true;
              });

              const runner = new Runner({
                traceIncludeSensitiveData: false,
                tracingDisabled: false,
                workflowName: "fitness-coach-turn",
              });
              const result = await runner.run(
                lead,
                toAgentInputItems(selectedMessages),
                {
                  context: runContext,
                  maxTurns: MAX_COACH_STEPS,
                  ...(signal ? { signal } : {}),
                  stream: true,
                },
              );

              for await (const event of result) {
                writeAgentStreamEvent(event, writer, textState);
              }
              await result.completed;
            } finally {
              if (tavilyServer !== null) await tavilyServer.close();
            }
          },
          {
            groupId: context.profile.user_id,
            metadata: { model: MODEL },
          },
        );
        finishAgentText(writer, textState);
        writer.write({ type: "finish-step" });
        writer.write({ type: "finish", finishReason: "stop" });
      } catch (error) {
        if (signal?.aborted) {
          writer.write({ type: "abort", reason: "request aborted" });
          return;
        }
        finishAgentText(writer, textState);
        writer.write({ type: "error", errorText: streamErrorMessage });
        writer.write({ type: "finish-step" });
        writer.write({ type: "finish", finishReason: "error" });
        const message = error instanceof Error ? error.message : String(error);
        console.error(
          "[chat] stream error:",
          message.replace(/key=[^&\s]+/g, "key=***"),
        );
      }
    },
  });

  return createUIMessageStreamResponse({ stream });
}
