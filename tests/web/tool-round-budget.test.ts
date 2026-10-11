import { Agent, RunContext, type MCPServer } from "@openai/agents";
import { describe, expect, it } from "vitest";

import {
  createAgentCoachTools,
  type CoachAgentRunContext,
} from "../../lib/agent/coach-tools";
import {
  listBudgetedMcpTools,
  MAX_COACH_TURNS,
  MAX_TOOL_ROUNDS,
  ToolRoundBudget,
} from "../../lib/agent/tool-round-budget";

// Only the members the SDK touches when listing and converting MCP tools.
function fakeTavilyServer(): MCPServer {
  return {
    name: "tavily",
    cacheToolsList: false,
    listTools: () =>
      Promise.resolve([
        {
          name: "tavily_search",
          description: "Search the web",
          inputSchema: {
            type: "object",
            properties: { query: { type: "string" } },
            required: ["query"],
            additionalProperties: false,
          },
        },
      ]),
  } as unknown as MCPServer;
}

function runContext(
  toolRounds = new ToolRoundBudget(),
): RunContext<CoachAgentRunContext> {
  return new RunContext<CoachAgentRunContext>({
    hasActivityFileAttachment: false,
    toolCalled: false,
    toolRounds,
  });
}

function spendRounds(budget: ToolRoundBudget, rounds: number): void {
  for (let round = 0; round < rounds; round += 1) {
    budget.noteModelCall();
    budget.noteToolStart("update_goals");
  }
}

describe("ToolRoundBudget", () => {
  it("counts parallel calls from one model call as a single round", () => {
    const budget = new ToolRoundBudget();
    budget.noteModelCall();
    for (let call = 0; call < 12; call += 1) {
      budget.noteToolStart("process_uploaded_file");
    }

    expect(budget.roundsUsed).toBe(1);
    expect(budget.hasRoundsLeft()).toBe(true);
    expect(budget.toolNames).toHaveLength(12);
  });

  it("is exhausted after the configured number of rounds", () => {
    const budget = new ToolRoundBudget();
    spendRounds(budget, MAX_TOOL_ROUNDS);

    expect(budget.roundsUsed).toBe(MAX_TOOL_ROUNDS);
    expect(budget.hasRoundsLeft()).toBe(false);
  });

  it("leaves exactly one tool-free model call within the runner limit", () => {
    expect(MAX_COACH_TURNS).toBe(MAX_TOOL_ROUNDS + 1);
  });
});

describe("lead agent tool exposure", () => {
  async function leadAgent(): Promise<Agent<CoachAgentRunContext>> {
    return new Agent<CoachAgentRunContext>({
      name: "Lead coach",
      instructions: "system prompt",
      tools: [
        ...createAgentCoachTools({ accessToken: "t", baseUrl: "http://x" }),
        ...(await listBudgetedMcpTools<CoachAgentRunContext>(
          fakeTavilyServer(),
        )),
      ],
    });
  }

  it("offers coach and web-search tools while rounds remain", async () => {
    const budget = new ToolRoundBudget();
    spendRounds(budget, MAX_TOOL_ROUNDS - 1);

    const names = (await (await leadAgent()).getAllTools(runContext(budget)))
      .map((candidate) => ("name" in candidate ? candidate.name : ""))
      .sort();

    expect(names).toContain("tavily_search");
    expect(names).toContain("update_goals");
  });

  // The invariant that makes MaxTurnsExceededError unreachable: once the budget
  // is spent, no tool of any type — coach or MCP — is offered, so the next model
  // call can only write the reply.
  it("offers no tools of any type once the budget is spent", async () => {
    const budget = new ToolRoundBudget();
    spendRounds(budget, MAX_TOOL_ROUNDS);

    await expect(
      (await leadAgent()).getAllTools(runContext(budget)),
    ).resolves.toEqual([]);
  });

  // Canary for the SDK behaviour listBudgetedMcpTools works around. If this
  // starts failing, the SDK now gates MCP tools itself and the wrapper can go.
  it("would leak MCP tools past the budget if passed via mcpServers", async () => {
    const budget = new ToolRoundBudget();
    spendRounds(budget, MAX_TOOL_ROUNDS);
    const agent = new Agent<CoachAgentRunContext>({
      name: "Lead coach",
      instructions: "system prompt",
      mcpServers: [fakeTavilyServer()],
      tools: createAgentCoachTools({ accessToken: "t", baseUrl: "http://x" }),
    });

    const names = (await agent.getAllTools(runContext(budget))).map(
      (candidate) => ("name" in candidate ? candidate.name : ""),
    );

    expect(names).toEqual(["tavily_search"]);
  });
});
