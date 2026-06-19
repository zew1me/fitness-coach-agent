import { openai } from "@ai-sdk/openai";
import * as Sentry from "@sentry/nextjs";
import {
  convertToModelMessages,
  generateText,
  Output,
  type UIMessage,
} from "ai";

import { selectMessagesForModel } from "./message-context";
import type { AgentModelPolicy } from "./model-policy";
import {
  type ContextSlices,
  type InternalSpecialistRole,
  type SpecialistReport,
  specialistReportSchema,
} from "./orchestration-types";
import { buildSpecialistPrompt } from "./system-prompt";

const SPECIALIST_ORDER: InternalSpecialistRole[] = [
  "intake",
  "nutrition",
  "recovery",
  "workout",
];

type RunSpecialistsOptions = {
  messagesAreModelSelected?: boolean;
  messages: UIMessage[];
  modelPolicy: AgentModelPolicy;
  roles: InternalSpecialistRole[];
  slices: ContextSlices;
};

function orderRoles(roles: InternalSpecialistRole[]): InternalSpecialistRole[] {
  const unique = new Set(roles);
  return SPECIALIST_ORDER.filter((role) => unique.has(role));
}

export async function runSpecialists({
  messagesAreModelSelected = false,
  messages,
  modelPolicy,
  roles,
  slices,
}: RunSpecialistsOptions): Promise<SpecialistReport[]> {
  const model = openai(modelPolicy.specialistModel);
  const selectedMessages = messagesAreModelSelected
    ? messages
    : selectMessagesForModel(messages);
  const orderedRoles = orderRoles(roles);
  const modelMessages = await convertToModelMessages(selectedMessages);
  const settledReports = await Promise.allSettled(
    orderedRoles.map(async (role) => {
      const { output } = await generateText({
        maxOutputTokens: 1024,
        maxRetries: 2,
        messages: modelMessages,
        model,
        output: Output.object({
          schema: specialistReportSchema,
        }),
        system: buildSpecialistPrompt(role, slices[role]),
        providerOptions: {
          openai: {
            reasoningEffort: modelPolicy.specialistReasoningEffort,
            store: true,
            textVerbosity: modelPolicy.specialistTextVerbosity,
          },
        },
        timeout: {
          totalMs: 30_000,
        },
      });

      return specialistReportSchema.parse(output);
    }),
  );

  return settledReports.flatMap((result, index) => {
    if (result.status === "fulfilled") {
      return [result.value];
    }

    const role = orderedRoles[index] ?? "unknown";
    const errorType =
      result.reason instanceof Error
        ? result.reason.name
        : typeof result.reason;
    Sentry.logger.warn("chat: specialist failed", {
      role,
      errorType,
      error:
        result.reason instanceof Error
          ? result.reason.message
          : String(result.reason),
    });
    return [];
  });
}
