import {
  mcpToFunctionTool,
  type Agent,
  type MCPServer,
  type RunContext,
  type Tool,
} from "@openai/agents";

/**
 * Model calls in one coach reply that may issue tool calls. Each round may hold
 * several parallel calls. The model call after the last round gets no tools, so
 * it must write the reply — that is what keeps a turn from spinning into
 * MaxTurnsExceededError (Sentry 7633993901).
 */
export const MAX_TOOL_ROUNDS = 2;

/**
 * Runner turn limit that fits the budget exactly: every model call before the
 * last can only continue the run by calling tools, so tool rounds plus the
 * tool-free reply is the most a turn can ever take.
 */
export const MAX_COACH_TURNS = MAX_TOOL_ROUNDS + 1;

export const FINAL_RESPONSE_INSTRUCTION = [
  "You have used every tool round for this reply, so no tools are available now.",
  "Write the final user-facing response: tell the athlete what changed, what was saved, or what result you used, and say plainly if anything could not be finished.",
  "End with one concise, context-aware prompt to continue the conversation.",
].join(" ");

/**
 * Counts tool rounds — model calls that issued at least one tool call — across
 * every tool source, so one predicate gates coach tools and MCP tools alike.
 *
 * The Agents SDK has no per-turn hook, so the turn boundary comes from the lead
 * agent's instructions function, which the runner resolves exactly once per model
 * call. Tool calls always start after the instructions for their own model call
 * have been resolved, so attributing each call to the current call count needs
 * no assumption about whether the runner evaluates tools or instructions first.
 */
export class ToolRoundBudget {
  readonly #maxRounds: number;
  readonly #roundsWithTools = new Set<number>();
  readonly #toolNames: string[] = [];
  #modelCalls = 0;

  constructor(maxRounds: number = MAX_TOOL_ROUNDS) {
    this.#maxRounds = maxRounds;
  }

  noteModelCall(): void {
    this.#modelCalls += 1;
  }

  noteToolStart(toolName: string): void {
    this.#roundsWithTools.add(this.#modelCalls);
    this.#toolNames.push(toolName);
  }

  hasRoundsLeft(): boolean {
    return this.#roundsWithTools.size < this.#maxRounds;
  }

  get roundsUsed(): number {
    return this.#roundsWithTools.size;
  }

  get toolNames(): readonly string[] {
    return this.#toolNames;
  }
}

export type ToolRoundBudgetContext = { toolRounds: ToolRoundBudget };

/**
 * Wraps an agent's instructions so each model call advances the budget's turn
 * boundary, and the tool-free call after the last round is told to reply.
 */
export function budgetedInstructions<TContext extends ToolRoundBudgetContext>(
  instructions: string,
): (runContext: RunContext<TContext>) => string {
  return (runContext) => {
    const { toolRounds } = runContext.context;
    toolRounds.noteModelCall();
    return toolRounds.hasRoundsLeft()
      ? instructions
      : `${instructions}\n\n${FINAL_RESPONSE_INSTRUCTION}`;
  };
}

/** Attributes every tool call the agent starts to the current tool round. */
export function trackToolRounds<TContext extends ToolRoundBudgetContext>(
  agent: Agent<TContext>,
): void {
  agent.on("agent_tool_start", (runContext, startedTool) => {
    runContext.context.toolRounds.noteToolStart(startedTool.name);
  });
}

/**
 * Exposes an MCP server's tools as function tools behind the round budget.
 *
 * Passing the server through `Agent.mcpServers` instead would bypass the budget:
 * the SDK's `getAllTools` consults `isEnabled` only for function tools and always
 * includes MCP tools. Its own callable `toolFilter` is no substitute either — with
 * `cacheToolsList` the *filtered* list is cached module-wide per server and agent
 * name, so the first evaluation would be reused by later turns and athletes.
 */
export async function listBudgetedMcpTools<
  TContext extends ToolRoundBudgetContext,
>(server: MCPServer): Promise<Tool<TContext>[]> {
  const mcpTools = await server.listTools();
  return mcpTools.map((mcpTool) => ({
    ...mcpToFunctionTool(mcpTool, server, false),
    isEnabled: (runContext: RunContext<TContext>) =>
      Promise.resolve(runContext.context.toolRounds.hasRoundsLeft()),
  }));
}
