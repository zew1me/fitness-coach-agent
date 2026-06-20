import { Agent, run } from "@openai/agents";
import type { UIMessage } from "ai";

import { toAgentInputItems } from "./agent-input";
import { selectMessagesForModel } from "./message-context";
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
  model: string;
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
  const orderedRoles = orderRoles(roles);
  const reports: SpecialistReport[] = [];

  for (const role of orderedRoles) {
    const agent = new Agent({
      name: `${role[0]?.toUpperCase()}${role.slice(1)} specialist`,
      instructions: buildSpecialistPrompt(role, slices[role]),
      model,
      outputType: specialistReportSchema,
    });
    const result = await run(agent, toAgentInputItems(selectedMessages), {
      maxTurns: 1,
    });

    if (!result.finalOutput) {
      throw new Error(`Agent ${role} failed to produce output`);
    }
    reports.push(specialistReportSchema.parse(result.finalOutput));
  }

  return reports;
}
