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
      messages: await convertToModelMessages(normalizedMessages),
      model,
      output: Output.object({
        schema: specialistReportSchema,
      }),
      system: buildSpecialistPrompt(role, slices[role]),
    });

    reports.push(specialistReportSchema.parse(output));
  }

  return reports;
}
