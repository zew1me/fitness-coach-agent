import { openai } from "@ai-sdk/openai";
import {
  convertToModelMessages,
  stepCountIs,
  streamText,
  type ToolSet,
  type UIMessage,
} from "ai";

import { createCoachTools } from "./coach-tools";
import { buildContextSlices } from "./context-slices";
import { routeTurnIntent } from "./intent-router";
import {
  convertUnsupportedFilePartsToText,
  selectMessagesForModel,
} from "./message-context";
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
  streamErrorMessage?: string;
  tavilyTools?: ToolSet;
};

const MAX_COACH_STEPS = 4;
const FINAL_RESPONSE_INSTRUCTION = [
  "You just completed a tool call.",
  "Do not call another tool in this step.",
  "Write the final user-facing response now: tell the athlete what changed, what was saved, or what result you used.",
  "End with one concise, context-aware prompt to continue the conversation.",
].join(" ");

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

function validateSpecialistReports(
  reports: SpecialistReport[],
): SpecialistReport[] {
  return specialistReportsSchema.parse(reports);
}

function filterLeadTools(coachTools: ToolSet): ToolSet {
  return coachTools;
}

function stepRequiresUserFacingResponse(step: {
  toolCalls: Array<{ toolName: string }>;
}): boolean {
  return step.toolCalls.length > 0;
}

function generateUuid(): string {
  return crypto.randomUUID();
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
  const selectedMessages = messagesAreModelSelected
    ? messages
    : selectMessagesForModel(messages);
  const normalizedMessages =
    convertUnsupportedFilePartsToText(selectedMessages);
  const intent = routeTurnIntent(latestUserText(selectedMessages), context);
  const slices = buildContextSlices(context);
  const specialistReports = validateSpecialistReports(
    await runSpecialists({
      messages: selectedMessages,
      messagesAreModelSelected: true,
      model,
      roles: intent.specialists,
      slices,
    }),
  );
  const coachTools = filterLeadTools(
    createCoachTools({
      accessToken,
      baseUrl,
      ...(extraHeaders ? { extraHeaders } : {}),
    }),
  );
  const systemPrompt = buildLeadCoachPrompt(context, specialistReports);

  const result = streamText({
    messages: await convertToModelMessages(normalizedMessages),
    model,
    system: systemPrompt,
    experimental_telemetry: {
      isEnabled: true,
      functionId: "fitness-coach-agent/streamCoachTurn",
      recordInputs: true,
      recordOutputs: true,
      metadata: {
        intent: intent.kind,
        specialistRoles: intent.specialists,
      },
    },
    tools: {
      ...coachTools,
      ...tavilyTools,
    },
    stopWhen: stepCountIs(MAX_COACH_STEPS),
    prepareStep: ({ steps }) => {
      if (!steps.some(stepRequiresUserFacingResponse)) {
        return undefined;
      }

      return {
        activeTools: [],
        system: `${systemPrompt}\n\n${FINAL_RESPONSE_INSTRUCTION}`,
      };
    },
    onError: ({ error }) => {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(
        "[chat] stream error:",
        msg.replace(/key=[^&\s]+/g, "key=***"),
      );
    },
  });

  return result.toUIMessageStreamResponse({
    generateMessageId: generateUuid,
    onError: () => streamErrorMessage,
    onFinish: async ({ responseMessage, finishReason, isAborted }) => {
      if (isAborted) return;
      // Persist the assistant turn as its full `parts[]` array so tool-call
      // pills, reasoning blocks, and file parts survive page reloads. Tool-only
      // finishes (no text or tool parts) have nothing to persist.
      const parts = responseMessage.parts;
      if (parts.length === 0) return;
      try {
        const response = await fetch(`${baseUrl}/api/chat/messages`, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${accessToken}`,
            "Content-Type": "application/json",
            ...(extraHeaders ?? {}),
          },
          body: JSON.stringify({
            id: responseMessage.id,
            role: "assistant",
            parts,
            metadata: {
              message_kind: "assistant_reply",
              finish_reason: finishReason,
              client_message_id: responseMessage.id,
            },
          }),
        });
        if (!response.ok) {
          console.error(
            "[chat] persist assistant reply failed:",
            response.status,
          );
        }
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        console.error("[chat] persist assistant reply error:", msg);
      }
    },
    originalMessages: selectedMessages,
  });
}
