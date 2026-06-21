import { Agent, run, type AgentInputItem } from "@openai/agents";

import {
  delegationPlanSchema,
  type DelegationPlan,
} from "./orchestration-types";
import type { AthleteContextBundle } from "./types";
import { recordStageUsage } from "./usage-metrics";

const SPECIALISTS = {
  intake: "goals, availability, constraints, onboarding, and schedule",
  nutrition: "fueling, hydration, dietary constraints, and race nutrition",
  recovery: "sleep, readiness, fatigue, illness, injury risk, and recovery",
  workout:
    "training prescription, plan changes, load, thresholds, and workouts",
} as const;

export async function planSpecialistDelegation(options: {
  durableContext: AgentInputItem[];
  latestUserTurn: AgentInputItem[];
  coachingMemory: Array<Record<string, unknown>>;
  athleteContext: AthleteContextBundle;
  model: string;
}): Promise<DelegationPlan> {
  const planner = new Agent({
    name: "Lead coach delegation planner",
    model: options.model,
    outputType: delegationPlanSchema,
    instructions: [
      "Select zero, one, or two specialists needed for this athlete turn.",
      "Create an objective-specific brief from durable conversation context, including concrete prior details, constraints, decisions, and unresolved questions that the specialist needs.",
      "Reference relevant coaching-memory IDs. Do not copy athlete metrics or other authoritative athlete data into a brief; the current role-specific data slice is attached separately.",
      "Do not invent context. Use no tools.",
      `Available specialists: ${JSON.stringify(SPECIALISTS)}.`,
      `Durable conversation context: ${JSON.stringify(options.durableContext)}.`,
      `Active coaching memory: ${JSON.stringify(options.coachingMemory)}.`,
      `Current athlete context (for selection only): ${JSON.stringify(options.athleteContext)}.`,
    ].join("\n"),
  });
  const result = await run(planner, options.latestUserTurn, { maxTurns: 1 });
  recordStageUsage("delegation", result.state.usage);
  if (!result.finalOutput)
    throw new Error("Delegation planner produced no output");
  return delegationPlanSchema.parse(result.finalOutput);
}
