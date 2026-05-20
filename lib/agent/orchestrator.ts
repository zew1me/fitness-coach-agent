import { openai } from "@ai-sdk/openai";
import { convertToModelMessages, streamText, type ToolSet, type UIMessage } from "ai";

import { createCoachTools } from "./coach-tools";
import { buildContextSlices } from "./context-slices";
import { routeTurnIntent } from "./intent-router";
import { selectMessagesForModel } from "./message-context";
import { specialistReportsSchema, type SpecialistReport } from "./orchestration-types";
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
  streamErrorMessage?: string;
  tavilyTools?: ToolSet;
};

function latestUserText(messages: UIMessage[]): string {
  const latest = [...messages].reverse().find((message) => message.role === "user");
  if (!latest) return "";

  return latest.parts
    .map((part) => (part.type === "text" ? part.text : ""))
    .filter(Boolean)
    .join("\n");
}

function validateSpecialistReports(reports: SpecialistReport[]): SpecialistReport[] {
  return specialistReportsSchema.parse(reports);
}

function filterLeadTools(coachTools: ToolSet): ToolSet {
  return coachTools;
}

export async function streamCoachTurn({
  accessToken,
  baseUrl,
  context,
  extraHeaders,
  messages,
  messagesAreModelSelected = false,
  streamErrorMessage = "Coach is unavailable right now. Please try again.",
  tavilyTools = {},
}: StreamCoachTurnOptions): Promise<Response> {
  const model = openai("gpt-5-mini");
  const selectedMessages = messagesAreModelSelected ? messages : selectMessagesForModel(messages);
  const intent = routeTurnIntent(latestUserText(selectedMessages), context);
  const slices = buildContextSlices(context);
  const specialistReports = validateSpecialistReports(
    await runSpecialists({
      messages: selectedMessages,
      messagesAreModelSelected: true,
      model,
      roles: intent.specialists,
      slices,
    })
  );
  const coachTools = filterLeadTools(
    createCoachTools({
      accessToken,
      baseUrl,
      ...(extraHeaders ? { extraHeaders } : {}),
    })
  );

  const result = streamText({
    messages: await convertToModelMessages(selectedMessages),
    model,
    system: buildLeadCoachPrompt(context, specialistReports),
    tools: {
      ...coachTools,
      ...tavilyTools,
    },
    onError: ({ error }) => {
      const msg = error instanceof Error ? error.message : String(error);
      console.error("[chat] stream error:", msg.replace(/key=[^&\s]+/g, "key=***"));
    },
    onFinish: async ({ text, finishReason }) => {
      const trimmed = text.trim();
      if (trimmed.length === 0) return;
      try {
        const response = await fetch(`${baseUrl}/api/chat/messages`, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${accessToken}`,
            "Content-Type": "application/json",
            ...(extraHeaders ?? {}),
          },
          body: JSON.stringify({
            role: "assistant",
            content: trimmed,
            metadata: { message_kind: "assistant_reply", finish_reason: finishReason },
          }),
        });
        if (!response.ok) {
          console.error("[chat] persist assistant reply failed:", response.status);
        }
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        console.error("[chat] persist assistant reply error:", msg);
      }
    },
  });

  return result.toUIMessageStreamResponse({
    onError: () => streamErrorMessage,
  });
}
