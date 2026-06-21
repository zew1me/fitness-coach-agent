import type { UIMessage } from "ai";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { runSpecialists } from "../../lib/agent/specialists";

const agentsMocks = vi.hoisted(() => {
  const constructedAgents: Array<Record<string, unknown>> = [];
  const run = vi.fn((agent: { name: string }) =>
    Promise.resolve({
      state: { usage: undefined },
      finalOutput: {
        confidence: "high",
        proposedUpdates: [],
        risks: [],
        role: agent.name.toLowerCase().replace(" specialist", ""),
        summary: `${agent.name} report`,
      },
    }),
  );

  class Agent {
    name: string;

    constructor(config: Record<string, unknown>) {
      this.name = String(config["name"]);
      constructedAgents.push(config);
    }
  }

  return { Agent, constructedAgents, run };
});

vi.mock("@openai/agents", () => ({
  Agent: agentsMocks.Agent,
  run: agentsMocks.run,
}));

beforeEach(() => {
  agentsMocks.constructedAgents.length = 0;
  vi.clearAllMocks();
});

describe("runSpecialists with the Agents SDK", () => {
  it("runs only the deterministically selected specialists in safety order", async () => {
    const messages: UIMessage[] = [
      {
        id: "message-1",
        parts: [{ type: "text", text: "I am sore after today's workout." }],
        role: "user",
      },
    ];

    const reports = await runSpecialists({
      messages,
      model: "gpt-5.4-mini",
      roles: ["workout", "recovery"],
      delegations: [
        {
          role: "recovery",
          objective: "Assess whether soreness changes tomorrow's session",
          conversationDetails: [
            "Athlete said the soreness began after hill repeats",
          ],
          constraintsAndPriorDecisions: ["Keep Friday as a rest day"],
          unresolvedQuestions: ["Is soreness focal or general?"],
          relevantCoachingMemoryIds: ["memory-1"],
        },
        {
          role: "workout",
          objective: "Propose a safe adjustment",
          conversationDetails: ["Athlete prefers cycling substitutions"],
          constraintsAndPriorDecisions: [],
          unresolvedQuestions: [],
          relevantCoachingMemoryIds: [],
        },
      ],
      coachingMemory: [{ id: "memory-1", statement: "Friday remains rest" }],
      slices: {
        intake: {
          goals: [],
          profile: {
            coaching_state: "active",
            display_name: undefined,
            primary_sports: [],
            weekly_available_hours: undefined,
          },
          schedule: null,
        },
        lead: {
          active_plan: null,
          computed_age: null,
          current_load: null,
          goals: [],
          profile: {
            coaching_state: "active",
            display_name: undefined,
            primary_sports: [],
            weekly_available_hours: undefined,
          },
        },
        nutrition: {
          computed_age: null,
          profile: {
            biological_sex: undefined,
            dietary_restrictions: undefined,
            hormone_status: undefined,
            nutrition_notes: undefined,
          },
        },
        recovery: {
          computed_age: null,
          ctl_ceiling_guidance: {
            age_bracket: "unknown",
            committed_amateur_ctl: 0,
            elite_ctl: 0,
            notes: "",
            recovery_week_frequency: "",
            recreational_ctl: 0,
          },
          current_load: null,
          recent_recovery: [],
        },
        workout: {
          active_plan: null,
          ctl_ceiling_guidance: {
            age_bracket: "unknown",
            committed_amateur_ctl: 0,
            elite_ctl: 0,
            notes: "",
            recovery_week_frequency: "",
            recreational_ctl: 0,
          },
          current_load: null,
          goals: [],
          profile: { primary_sports: [], weekly_available_hours: undefined },
          schedule: null,
          thresholds: [],
        },
      },
    });

    expect(agentsMocks.constructedAgents.map((agent) => agent["name"])).toEqual(
      ["Recovery specialist", "Workout specialist"],
    );
    expect(agentsMocks.run).toHaveBeenCalledTimes(2);
    expect(reports.map((report) => report.role)).toEqual([
      "recovery",
      "workout",
    ]);
    expect(agentsMocks.constructedAgents[0]?.["instructions"]).toContain(
      "Athlete said the soreness began after hill repeats",
    );
    expect(agentsMocks.constructedAgents[0]?.["instructions"]).toContain(
      "Friday remains rest",
    );
  });
});
