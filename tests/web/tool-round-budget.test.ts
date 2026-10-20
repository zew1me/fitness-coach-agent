import {
  Agent,
  RunContext,
  Runner,
  type MCPServer,
  type Model,
  type ModelRequest,
  type ModelResponse,
} from "@openai/agents";
import { describe, expect, it } from "vitest";

import {
  createAgentCoachTools,
  type CoachAgentRunContext,
} from "../../lib/agent/coach-tools";
import {
  budgetedInstructions,
  FINAL_RESPONSE_INSTRUCTION,
  listBudgetedMcpTools,
  MAX_COACH_TURNS,
  MAX_TOOL_ROUNDS,
  ToolRoundBudget,
  trackToolRounds,
} from "../../lib/agent/tool-round-budget";

// Only the members the SDK touches when listing and converting MCP tools.
function fakeTavilyServer(): MCPServer {
  return {
    name: "tavily",
    cacheToolsList: false,
    callTool: () => Promise.resolve([{ type: "text", text: "search result" }]),
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

type StreamEvent = Awaited<
  ReturnType<Model["getStreamedResponse"]> extends AsyncIterable<infer E>
    ? E
    : never
>;

const USAGE = { requests: 1, inputTokens: 0, outputTokens: 0, totalTokens: 0 };

/**
 * The worst-case model: whenever it is offered tools it calls two of them in
 * parallel instead of answering — the behaviour behind Sentry 7633993901.
 */
class ToolHungryModel implements Model {
  readonly requests: ModelRequest[] = [];

  getResponse(): Promise<ModelResponse> {
    return Promise.reject(new Error("the coach turn always streams"));
  }

  async *getStreamedResponse(
    request: ModelRequest,
  ): AsyncIterable<StreamEvent> {
    await Promise.resolve();
    this.requests.push(request);
    const call = this.requests.length;
    const output =
      request.tools.length === 0
        ? [
            {
              type: "message" as const,
              role: "assistant" as const,
              status: "completed" as const,
              content: [
                { type: "output_text" as const, text: "Here's where we are." },
              ],
            },
          ]
        : ["get_active_plan", "tavily_search"].map((name) => ({
            type: "function_call" as const,
            callId: `call-${call}-${name}`,
            name,
            arguments: name === "tavily_search" ? '{"query":"race"}' : "{}",
            status: "completed" as const,
          }));
    yield {
      type: "response_done",
      response: { id: `resp-${call}`, usage: USAGE, output },
    } as StreamEvent;
  }
}

describe("lead coach run under the tool-round budget", () => {
  // End to end through the real Runner: the wiring (instructions as the turn
  // boundary, agent_tool_start as the round counter) is what keeps the run
  // inside maxTurns, so it is exercised here rather than simulated.
  it("forces a tool-free reply instead of exceeding max turns", async () => {
    const model = new ToolHungryModel();
    const toolRounds = new ToolRoundBudget();
    const agent = new Agent<CoachAgentRunContext>({
      name: "Lead coach",
      instructions: budgetedInstructions<CoachAgentRunContext>("system prompt"),
      model,
      tools: [
        ...createAgentCoachTools({
          accessToken: "t",
          baseUrl: "http://x",
          fetchImpl: () => Promise.resolve(new Response("{}", { status: 200 })),
        }),
        ...(await listBudgetedMcpTools<CoachAgentRunContext>(
          fakeTavilyServer(),
        )),
      ],
    });
    trackToolRounds(agent);

    const result = await new Runner({ tracingDisabled: true }).run(
      agent,
      "Find me info on my race",
      {
        context: {
          hasActivityFileAttachment: false,
          toolCalled: false,
          toolRounds,
        },
        maxTurns: MAX_COACH_TURNS,
        stream: true,
      },
    );
    for await (const event of result) void event;
    await result.completed;

    expect(result.finalOutput).toBe("Here's where we are.");
    expect(model.requests).toHaveLength(MAX_COACH_TURNS);
    expect(toolRounds.roundsUsed).toBe(MAX_TOOL_ROUNDS);
    expect(toolRounds.toolNames).toHaveLength(2 * MAX_TOOL_ROUNDS);

    const offered = model.requests.map((request) =>
      request.tools.map((candidate) => candidate.name),
    );
    // Activity ingestion is first-round only: uploads have no dedup yet.
    expect(offered[0]).toContain("process_uploaded_file");
    expect(offered[1]).not.toContain("process_uploaded_file");
    expect(offered[1]).toContain("tavily_search");
    expect(offered[MAX_TOOL_ROUNDS]).toEqual([]);
    expect(model.requests[MAX_TOOL_ROUNDS]?.systemInstructions).toBe(
      `system prompt\n\n${FINAL_RESPONSE_INSTRUCTION}`,
    );
  });
});
