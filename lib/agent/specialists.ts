import {
  convertToModelMessages,
  generateText,
  Output,
  type LanguageModel,
  type UIMessage,
} from "ai";

import {
  convertUnsupportedFilePartsToText,
  selectMessagesForModel,
} from "./message-context";
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
  model: LanguageModel;
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
  model,
  modelPolicy,
  roles,
  slices,
}: RunSpecialistsOptions): Promise<SpecialistReport[]> {
  const selectedMessages = messagesAreModelSelected
    ? messages
    : selectMessagesForModel(messages);
  const normalizedMessages =
    convertUnsupportedFilePartsToText(selectedMessages);
  const orderedRoles = orderRoles(roles);
  const reports: SpecialistReport[] = [];

  for (const role of orderedRoles) {
    const { output } = await generateText({
      maxOutputTokens: 1024,
      maxRetries: 2,
      messages: await convertToModelMessages(normalizedMessages),
      model,
      output: Output.object({
        schema: specialistReportSchema,
      }),
      system: buildSpecialistPrompt(role, slices[role]),
      providerOptions: {
        openai: {
          reasoningEffort: modelPolicy.specialistReasoningEffort,
          store: true,
          textVerbosity: "low",
        },
      },
      timeout: {
        totalMs: 30_000,
      },
    });

    reports.push(specialistReportSchema.parse(output));
  }

  return reports;
}
