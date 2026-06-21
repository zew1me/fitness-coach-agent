import { Agent, run } from "@openai/agents";
import type { UIMessage } from "ai";

import { toAgentInputItems } from "./agent-input";
import { selectMessagesForModel } from "./message-context";
import {
  type ContextSlices,
  type InternalSpecialistRole,
  type SpecialistReport,
  type SpecialistDelegation,
  specialistReportSchema,
} from "./orchestration-types";
import { buildSpecialistPrompt } from "./system-prompt";
import { recordStageUsage } from "./usage-metrics";

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
  delegations?: SpecialistDelegation[];
  coachingMemory?: Array<Record<string, unknown>>;
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
  delegations,
  coachingMemory = [],
}: RunSpecialistsOptions): Promise<SpecialistReport[]> {
  const selectedMessages = messagesAreModelSelected
    ? messages
    : selectMessagesForModel(messages);
  const orderedRoles = orderRoles(roles);
  const reports: SpecialistReport[] = [];

  for (const role of orderedRoles) {
    const delegation = delegations?.find(
      (candidate) => candidate.role === role,
    );
    const relevantMemory = coachingMemory.filter((record) =>
      delegation?.relevantCoachingMemoryIds.includes(String(record["id"])),
    );
    const agent = new Agent({
      name: `${role[0]?.toUpperCase()}${role.slice(1)} specialist`,
      instructions: [
        buildSpecialistPrompt(role, slices[role]),
        `Lead-generated brief: ${JSON.stringify(delegation ?? {})}`,
        `Relevant coaching memory: ${JSON.stringify(relevantMemory)}`,
      ].join("\n\n"),
      model,
      outputType: specialistReportSchema,
    });
    const result = await run(agent, toAgentInputItems(selectedMessages), {
      maxTurns: 1,
    });
    recordStageUsage("specialist", result.state.usage);

    if (!result.finalOutput) {
      throw new Error(`Agent ${role} failed to produce output`);
    }
    reports.push(specialistReportSchema.parse(result.finalOutput));
  }

  return reports;
}
