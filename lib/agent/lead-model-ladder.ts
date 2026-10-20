import {
  Agent,
  MaxTurnsExceededError,
  Runner,
  withTrace,
  type AgentInputItem,
  type Tool,
  type RunStreamEvent,
} from "@openai/agents";
import * as Sentry from "@sentry/nextjs";
import type { UIMessage, UIMessageStreamWriter } from "ai";

import { toAgentInputItems } from "./agent-input";
import { writeAgentStreamEvent } from "./agent-stream";
import {
  createAgentCoachTools,
  type CoachAgentRunContext,
} from "./coach-tools";
import {
  oldestDueFollowUp,
  type CoachingMemoryRecord,
} from "./coaching-memory";
import type { DurableCompactionSession } from "./durable-compaction-session";
import {
  modelCircuitBreaker,
  type ModelAdmission,
} from "./model-circuit-breaker";
import {
  buildModelSettings,
  captureRateLimit,
  getRetryAfterMs,
  isRateLimitError,
  MODEL_TIERS,
  type ModelTier,
} from "./model-tiers";
import type { SpecialistReport } from "./orchestration-types";
import type { SupabaseAgentSession } from "./supabase-agent-session";
import { buildLeadCoachPrompt } from "./system-prompt";
import {
  budgetedInstructions,
  MAX_COACH_TURNS,
  trackToolRounds,
} from "./tool-round-budget";
import type { AthleteContextBundle } from "./types";
import { recordStageUsage } from "./usage-metrics";

const ACKNOWLEDGEMENT_PROMPT =
  "You just completed an action for the athlete. Use the prior tool result in the conversation to write a brief 1-2 sentence acknowledgement of what changed, then end with one short question or prompt to continue. Do not call tools. Be warm and concise.";
const EMPTY_MODEL_RESPONSE = "Hey, can you remind me of where we are at?";

export type StreamTextState = {
  lastToolName?: string;
  textId: string;
  textStarted: boolean;
};

type TurnRateLimit = { error: unknown; retryAfterMs?: number; tier: ModelTier };

// Every 429 the turn saw (delegation planner, specialists, lead, acknowledgement),
// plus which models the circuit breaker has already been told about, so each model
// records at most one breaker failure per turn.
type TurnRateLimitLedger = {
  flushToBreaker: (startingAdmission: ModelAdmission) => void;
  forModel: (model: string) => TurnRateLimit | undefined;
  longestLeadRetryAfterMs: () => number | undefined;
  note: (tier: ModelTier, error: unknown) => void;
  noteLeadRetryAfter: (retryAfterMs: number | undefined) => void;
  recordBreakerFailureOnce: (
    admission: ModelAdmission,
    retryAfterMs: number | undefined,
  ) => void;
};

// Turn state the ladder shares by reference with the stream lifecycle in
// orchestrator.ts: the outer catch reads the longest lead retry delay and text
// state, and the turn's `finally` releases every probe admitted here.
export type LeadTurnState = {
  admittedProbes: Set<ModelAdmission>;
  effectiveSignal: AbortSignal;
  isTurnAborted: () => boolean;
  rateLimits: TurnRateLimitLedger;
  textState: StreamTextState;
};

type LeadTurn = {
  accessToken: string;
  baseUrl: string;
  coachingMemory: CoachingMemoryRecord[];
  context: AthleteContextBundle;
  durableFallbackBaseline: AgentInputItem[] | undefined;
  durableSession: DurableCompactionSession | undefined;
  extraHeaders: Record<string, string> | undefined;
  lifecycle: LeadTurnState;
  reports: SpecialistReport[];
  runContext: CoachAgentRunContext;
  selectedMessages: UIMessage[];
  tavilyTools: Tool<CoachAgentRunContext>[];
  timeZone: string | undefined;
  traceGroupId: string;
  underlyingSession: SupabaseAgentSession | undefined;
  writer: UIMessageStreamWriter;
};

type AttemptProgress = { eventStarted: boolean };

// The slice of the SDK's streamed run result the lead helpers read. Naming
// StreamedRunResult directly trips its Agent output-type constraint under
// exactOptionalPropertyTypes, while the inferred `runner.run` result fits this.
type LeadRunResult = AsyncIterable<RunStreamEvent> & {
  readonly completed: Promise<void>;
  readonly output: AgentInputItem[];
};

export function rateLimitStreamErrorMessage(
  error: unknown,
  fallback: string,
  priorRetryAfterMs?: number,
): string {
  if (!isRateLimitError(error)) return fallback;
  const errorRetryAfterMs = getRetryAfterMs(error);
  const retryAfterMs = Math.max(errorRetryAfterMs ?? 0, priorRetryAfterMs ?? 0);
  if (retryAfterMs <= 0) return fallback;

  const seconds = Math.max(1, Math.ceil(retryAfterMs / 1_000));
  const minutes = Math.ceil(seconds / 60);
  const wait =
    seconds < 60
      ? `${seconds} second${seconds === 1 ? "" : "s"}`
      : `${minutes} minute${minutes === 1 ? "" : "s"}`;
  return `Coach is rate-limited right now. Please try again in about ${wait}.`;
}

function buildToolAcknowledgement(toolName: string | undefined): string {
  const acknowledgements: Record<string, string> = {
    adjust_plan:
      "I've adjusted your plan. Want to review the changes or tune anything else?",
    calculate_zones: "Ok I've made some tweaks to your targets.",
    estimate_thresholds:
      "Thanks, I've factored the data you've shared into my notes.",
    generate_training_plan: "I've got a fresh training for you.",
    process_uploaded_file: "I've processed that file.",
    recalibrate_thresholds:
      "I checked your thresholds against recent efforts and noted the result.",
    save_activity_from_text: "I took note of that activity.",
    update_athlete_profile:
      "Thanks, I'll keep track of that information. Anything else you'd like to share with me at this time?",
    update_goals: "Ok, I'm tracking that as a goal of yours.",
    update_schedule:
      "Ok, made some notes based on your availability. We can use this to adjust your upcoming workouts.",
  };
  return (
    acknowledgements[toolName ?? ""] ?? "Done. What would you like to do next?"
  );
}

function buildRateLimitedToolAcknowledgement(
  toolName: string | undefined,
  error: unknown,
): string {
  const acknowledgement = buildToolAcknowledgement(toolName);
  const waitMessage = rateLimitStreamErrorMessage(error, "");
  return waitMessage ? `${acknowledgement} ${waitMessage}` : acknowledgement;
}

export function writeDeterministicText(
  writer: UIMessageStreamWriter,
  state: StreamTextState,
  text: string,
): void {
  if (!state.textStarted) {
    writer.write({ type: "text-start", id: state.textId });
    state.textStarted = true;
  }
  writer.write({ type: "text-delta", id: state.textId, delta: text });
}

export function createTurnRateLimitLedger(): TurnRateLimitLedger {
  const rateLimits = new Map<string, TurnRateLimit>();
  const breakerRecordedModels = new Set<string>();
  let longestLeadRetryAfterMs: number | undefined;
  const recordBreakerFailureOnce = (
    admission: ModelAdmission,
    retryAfterMs: number | undefined,
  ): void => {
    if (breakerRecordedModels.has(admission.tier.model)) return;
    modelCircuitBreaker.recordFailure(admission, retryAfterMs);
    breakerRecordedModels.add(admission.tier.model);
  };
  return {
    // A noted rate limit the ladder never reported (a specialist-tier 429, or
    // one on a tier whose attempt then failed for another reason) still counts
    // against that model once the turn ends.
    flushToBreaker: (startingAdmission): void => {
      for (const rateLimit of rateLimits.values()) {
        recordBreakerFailureOnce(
          rateLimit.tier.model === startingAdmission.tier.model
            ? startingAdmission
            : { tier: rateLimit.tier },
          rateLimit.retryAfterMs,
        );
      }
    },
    forModel: (model) => rateLimits.get(model),
    longestLeadRetryAfterMs: () => longestLeadRetryAfterMs,
    note: (tier, error): void => {
      const retryAfterMs = getRetryAfterMs(error);
      const existing = rateLimits.get(tier.model);
      if (existing && (existing.retryAfterMs ?? 0) >= (retryAfterMs ?? 0)) {
        return;
      }
      rateLimits.set(tier.model, {
        tier,
        error,
        ...(retryAfterMs === undefined ? {} : { retryAfterMs }),
      });
    },
    noteLeadRetryAfter: (retryAfterMs): void => {
      if (
        retryAfterMs !== undefined &&
        (longestLeadRetryAfterMs === undefined ||
          retryAfterMs > longestLeadRetryAfterMs)
      ) {
        longestLeadRetryAfterMs = retryAfterMs;
      }
    },
    recordBreakerFailureOnce,
  };
}

export function tierSpanAttributes(
  tier: ModelTier,
): Record<string, string | number> {
  return {
    "gen_ai.request.model": tier.model,
    "gen_ai.request.reasoning_effort": String(tier.effort),
    "coach.model_tier": MODEL_TIERS.indexOf(tier) + 1,
    "coach.breaker_state": modelCircuitBreaker.snapshot(tier.model).state,
  };
}

function ranTool(turn: LeadTurn): boolean {
  return (
    turn.runContext.toolCalled ||
    turn.lifecycle.textState.lastToolName !== undefined
  );
}

function createLeadAgent(
  turn: LeadTurn,
  tier: ModelTier,
): Agent<CoachAgentRunContext> {
  const lead = new Agent<CoachAgentRunContext>({
    name: "Lead coach",
    // Resolved once per model call, which makes it the turn boundary for the
    // tool-round budget.
    instructions: budgetedInstructions<CoachAgentRunContext>(
      buildLeadCoachPrompt(
        turn.context,
        turn.reports,
        oldestDueFollowUp(turn.coachingMemory)?.statement,
        turn.timeZone,
      ),
    ),
    model: tier.model,
    modelSettings: buildModelSettings(tier),
    // Tavily rides in `tools`, never `mcpServers`: the SDK ignores isEnabled for
    // MCP tools, so they would escape the round budget (see listBudgetedMcpTools).
    tools: [
      ...createAgentCoachTools({
        accessToken: turn.accessToken,
        baseUrl: turn.baseUrl,
        ...(turn.extraHeaders ? { extraHeaders: turn.extraHeaders } : {}),
        ...(turn.underlyingSession
          ? { modelSession: turn.underlyingSession }
          : {}),
      }),
      ...turn.tavilyTools,
    ],
  });
  trackToolRounds(lead);
  lead.on("agent_tool_start", (activeContext) => {
    activeContext.context.toolCalled = true;
  });
  return lead;
}

async function drainLeadStream(
  turn: LeadTurn,
  result: LeadRunResult,
  progress: AttemptProgress,
): Promise<void> {
  // The tool-round budget makes MaxTurnsExceededError unreachable in normal
  // operation; this is the backstop if that invariant ever breaks. The runner
  // throws only after the turn's tool calls have completed, so fall through to
  // the acknowledgement/deterministic response to preserve that work.
  try {
    for await (const event of result) {
      progress.eventStarted = true;
      writeAgentStreamEvent(event, turn.writer, turn.lifecycle.textState);
    }
    await result.completed;
  } catch (error) {
    if (!(error instanceof MaxTurnsExceededError)) {
      throw error;
    }
    if (!turn.lifecycle.isTurnAborted()) {
      const { toolRounds } = turn.runContext;
      const extra = {
        max_turns: MAX_COACH_TURNS,
        tool_names: toolRounds.toolNames.join(","),
        tool_rounds_used: toolRounds.roundsUsed,
      };
      // Error, not warning: the tool-round budget makes this unreachable, so
      // reaching it means that invariant broke — even though the athlete still
      // gets a reply from the salvage path. Its own fingerprint keeps it apart
      // from the pre-budget spin events.
      Sentry.captureException(error, {
        extra,
        fingerprint: ["coach-max-turns-salvaged"],
        level: "error",
        tags: { subsystem: "coach-max-turns" },
      });
      Sentry.logger.warn(
        "coach: max turns exceeded; salvaging completed tool work",
        extra,
      );
    }
  }
}

function reportAcknowledgementFailure(
  turn: LeadTurn,
  admission: ModelAdmission,
  error: unknown,
): void {
  const { tier } = admission;
  if (isRateLimitError(error)) {
    turn.lifecycle.rateLimits.note(tier, error);
    turn.lifecycle.rateLimits.recordBreakerFailureOnce(
      admission,
      getRetryAfterMs(error),
    );
    captureRateLimit({
      tier,
      outcome: "exhausted",
      error,
      textStarted: turn.lifecycle.textState.textStarted,
    });
  } else {
    Sentry.captureException(error, {
      tags: { subsystem: "coach-followup" },
    });
  }
  Sentry.logger.warn(
    "coach: acknowledgement follow-up failed; using deterministic fallback",
  );
}

/**
 * Asks the same tier for a short acknowledgement after a tool-only lead run.
 * Resolves to the failure (if any) so the deterministic fallback can mention a
 * rate-limit wait; a failure never fails the attempt.
 */
async function runAcknowledgementFollowup(
  turn: LeadTurn,
  admission: ModelAdmission,
  runner: Runner,
  leadResult: LeadRunResult,
): Promise<unknown> {
  const { tier } = admission;
  try {
    const acknowledgement = new Agent<CoachAgentRunContext>({
      name: "Coach acknowledgement",
      instructions: ACKNOWLEDGEMENT_PROMPT,
      model: tier.model,
      modelSettings: buildModelSettings(tier),
      mcpServers: [],
      tools: [],
    });
    const followup = await runner.run(acknowledgement, leadResult.output, {
      context: turn.runContext,
      maxTurns: 2,
      signal: turn.lifecycle.effectiveSignal,
      stream: true,
    });
    for await (const event of followup) {
      writeAgentStreamEvent(event, turn.writer, turn.lifecycle.textState);
    }
    await followup.completed;
    recordStageUsage("lead-followup", followup.state.usage);
    return undefined;
  } catch (error) {
    if (!turn.lifecycle.isTurnAborted()) {
      reportAcknowledgementFailure(turn, admission, error);
    }
    return error;
  }
}

function writeFallbackIfSilent(
  turn: LeadTurn,
  leadRanTool: boolean,
  acknowledgementError: unknown,
): void {
  const { textState } = turn.lifecycle;
  if (textState.textStarted || turn.lifecycle.isTurnAborted()) return;
  writeDeterministicText(
    turn.writer,
    textState,
    leadRanTool
      ? buildRateLimitedToolAcknowledgement(
          textState.lastToolName,
          acknowledgementError,
        )
      : EMPTY_MODEL_RESPONSE,
  );
}

// One tier attempt keeps streaming, tool acknowledgement, telemetry, and
// deterministic degradation inside the same trace boundary.
async function attemptLeadTier(
  turn: LeadTurn,
  admission: ModelAdmission,
  progress: AttemptProgress,
): Promise<void> {
  const lead = createLeadAgent(turn, admission.tier);
  const runner = new Runner({
    traceIncludeSensitiveData: true,
    tracingDisabled: false,
    workflowName: "fitness-coach-turn",
  });
  const result = await runner.run(
    lead,
    toAgentInputItems(turn.selectedMessages),
    {
      context: turn.runContext,
      maxTurns: MAX_COACH_TURNS,
      signal: turn.lifecycle.effectiveSignal,
      ...(turn.durableSession ? { session: turn.durableSession } : {}),
      stream: true,
    },
  );
  await drainLeadStream(turn, result, progress);
  recordStageUsage("lead", result.state.usage);

  const leadRanTool = ranTool(turn);
  const acknowledgementError =
    leadRanTool &&
    !turn.lifecycle.textState.textStarted &&
    !turn.lifecycle.isTurnAborted()
      ? await runAcknowledgementFollowup(turn, admission, runner, result)
      : undefined;
  writeFallbackIfSilent(turn, leadRanTool, acknowledgementError);
}

async function runLeadTierAttempt(
  turn: LeadTurn,
  admission: ModelAdmission,
  progress: AttemptProgress,
): Promise<void> {
  await withTrace(
    "fitness-coach-tier-attempt",
    () => attemptLeadTier(turn, admission, progress),
    {
      groupId: turn.traceGroupId,
      metadata: { model: admission.tier.model },
    },
  );
}

// Runs inside the attempt's try so an abort reaches the tier failure handler,
// exactly like an error thrown by the attempt itself.
function settleTierAttempt(turn: LeadTurn, admission: ModelAdmission): void {
  const { lifecycle } = turn;
  if (lifecycle.isTurnAborted()) {
    modelCircuitBreaker.releaseProbe(admission);
    throw lifecycle.effectiveSignal.reason instanceof Error
      ? lifecycle.effectiveSignal.reason
      : new Error("chat turn aborted");
  }
  // A 429 on this model from an auxiliary call (planner, specialist,
  // acknowledgement) still counts against it even though the lead answered.
  const auxiliaryRateLimit = lifecycle.rateLimits.forModel(
    admission.tier.model,
  );
  if (auxiliaryRateLimit) {
    lifecycle.rateLimits.recordBreakerFailureOnce(
      admission,
      auxiliaryRateLimit.retryAfterMs,
    );
  } else {
    modelCircuitBreaker.recordSuccess(admission);
  }
}

function canFallBack(
  turn: LeadTurn,
  admission: ModelAdmission,
  progress: AttemptProgress,
): boolean {
  return (
    !progress.eventStarted &&
    !turn.lifecycle.textState.textStarted &&
    !ranTool(turn) &&
    !turn.lifecycle.isTurnAborted() &&
    MODEL_TIERS.indexOf(admission.tier) + 1 < MODEL_TIERS.length
  );
}

function annotateActiveSpanWithTier(tier: ModelTier): void {
  for (const [key, value] of Object.entries(tierSpanAttributes(tier))) {
    Sentry.getActiveSpan()?.setAttribute(key, value);
  }
}

async function fallBackToNextTier(
  turn: LeadTurn,
  admission: ModelAdmission,
  error: unknown,
): Promise<ModelAdmission> {
  if (turn.durableFallbackBaseline && turn.underlyingSession) {
    await turn.underlyingSession.replaceAll(turn.durableFallbackBaseline, {
      trigger: "model-tier-fallback-rollback",
    });
  }
  const failedTierIndex = MODEL_TIERS.indexOf(admission.tier);
  const nextAdmission = modelCircuitBreaker.pickTier(
    MODEL_TIERS.slice(failedTierIndex + 1),
  );
  const nextTier = nextAdmission.tier;
  captureRateLimit({
    tier: admission.tier,
    outcome: "falling_back",
    error,
    fallbackModel: nextTier.model,
    textStarted: turn.lifecycle.textState.textStarted,
  });
  if (nextAdmission.probeGeneration !== undefined) {
    turn.lifecycle.admittedProbes.add(nextAdmission);
  }
  annotateActiveSpanWithTier(nextTier);
  Sentry.logger.warn(
    `coach: tier ${failedTierIndex + 1} rate-limited; falling back to ${nextTier.model}`,
  );
  return nextAdmission;
}

/**
 * Decides what a failed tier attempt means for the turn: rethrow it, answer
 * with a deterministic tool acknowledgement (resolves `undefined`), or resolve
 * the next tier's admission to fall back to.
 */
async function recoverFromTierFailure(
  turn: LeadTurn,
  admission: ModelAdmission,
  progress: AttemptProgress,
  error: unknown,
): Promise<ModelAdmission | undefined> {
  if (!isRateLimitError(error)) {
    modelCircuitBreaker.releaseProbe(admission);
    throw error;
  }

  const { tier } = admission;
  const { rateLimits, textState } = turn.lifecycle;
  const retryAfterMs = getRetryAfterMs(error);
  rateLimits.noteLeadRetryAfter(retryAfterMs);
  rateLimits.note(tier, error);
  rateLimits.recordBreakerFailureOnce(admission, retryAfterMs);
  if (
    !textState.textStarted &&
    ranTool(turn) &&
    !turn.lifecycle.isTurnAborted()
  ) {
    captureRateLimit({ tier, outcome: "exhausted", error, textStarted: false });
    writeDeterministicText(
      turn.writer,
      textState,
      buildRateLimitedToolAcknowledgement(textState.lastToolName, error),
    );
    return undefined;
  }
  if (!canFallBack(turn, admission, progress)) {
    captureRateLimit({
      tier,
      outcome: "exhausted",
      error,
      textStarted: textState.textStarted,
    });
    throw error;
  }
  return fallBackToNextTier(turn, admission, error);
}

// Resolves the next admission when this attempt fell back, or `undefined` once
// the turn is answered.
async function attemptTierOrFallBack(
  turn: LeadTurn,
  admission: ModelAdmission,
): Promise<ModelAdmission | undefined> {
  const progress: AttemptProgress = { eventStarted: false };
  try {
    await runLeadTierAttempt(turn, admission, progress);
    settleTierAttempt(turn, admission);
    return undefined;
  } catch (error) {
    return await recoverFromTierFailure(turn, admission, progress, error);
  }
}

/**
 * Runs the lead coach down the model ladder starting at `startingAdmission`,
 * falling back to the next admitted tier only while a rate-limited attempt has
 * produced no stream event, text, or tool call.
 */
export async function runLeadModelLadder(
  turn: LeadTurn,
  startingAdmission: ModelAdmission,
): Promise<void> {
  let admission: ModelAdmission | undefined = startingAdmission;
  while (admission !== undefined) {
    admission = await attemptTierOrFallBack(turn, admission);
  }
}
