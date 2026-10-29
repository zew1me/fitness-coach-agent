import {
  MCPServerStreamableHttp,
  withTrace,
  type AgentInputItem,
  type MCPServer,
  type Tool,
} from "@openai/agents";
import * as Sentry from "@sentry/nextjs";
import {
  createUIMessageStream,
  createUIMessageStreamResponse,
  type UIMessageStreamWriter,
  type UIMessage,
} from "ai";

import { createLogger, type Logger } from "../logger";

import { toAgentInputItems } from "./agent-input";
import { finishAgentText } from "./agent-stream";
import {
  isActivityFile,
  isZipUpload,
  type CoachAgentRunContext,
} from "./coach-tools";
import type { CoachingMemoryRecord } from "./coaching-memory";
import { buildContextSlices } from "./context-slices";
import { planSpecialistDelegation } from "./delegation-planner";
import {
  DEFAULT_AUTO_COMPACT_TOKENS,
  DurableCompactionSession,
  estimateStoredContext,
} from "./durable-compaction-session";
import { fetchSignalWithTimeout } from "./fetch-signal";
import {
  createTurnRateLimitLedger,
  rateLimitStreamErrorMessage,
  runLeadModelLadder,
  tierSpanAttributes,
  writeDeterministicText,
  type LeadTurnState,
  type StreamTextState,
} from "./lead-model-ladder";
import {
  CHAT_TURN_LEASE_TTL_SECONDS,
  LeaseAcquisitionError,
  LeaseRenewalError,
  acquireChatTurnLease,
  releaseChatTurnLease,
  renewChatTurnLease,
  type ChatTurnLeaseState,
} from "./lease-client";
import { nonImageFilePart, selectMessagesForModel } from "./message-context";
import {
  modelCircuitBreaker,
  type ModelAdmission,
} from "./model-circuit-breaker";
import {
  captureRateLimit,
  isRateLimitError,
  MODEL_TIERS,
  resolveSpecialistTier,
  type ModelTier,
} from "./model-tiers";
import {
  type DelegationPlan,
  type SpecialistReport,
} from "./orchestration-types";
import { runSpecialists } from "./specialists";
import {
  ModelStateError,
  SupabaseAgentSession,
} from "./supabase-agent-session";
import { listBudgetedMcpTools, ToolRoundBudget } from "./tool-round-budget";
import type { AthleteContextBundle } from "./types";

type StreamCoachTurnBaseOptions = {
  accessToken: string;
  baseUrl: string;
  context: AthleteContextBundle;
  extraHeaders?: Record<string, string>;
  // Defaults to the Sentry + console fan-out; tests inject a spy or no-op.
  logger?: Logger;
  messages: UIMessage[];
  messagesAreModelSelected?: boolean;
  signal?: AbortSignal;
  streamErrorMessage?: string;
  tavilyMcpUrl?: string;
  // Undefined is permitted explicitly because requests without this optional
  // browser header still need the deterministic UTC prompt fallback.
  timeZone?: string | undefined;
  useDurableSession?: boolean;
};

// acquiredLease was obtained under a specific leaseId; the two must travel together
// so renewal/release target the lease that was actually acquired. The independent-
// acquisition variant (no acquiredLease yet) may still supply an optional leaseId
// up front, or let one be generated.
export type StreamCoachTurnOptions = StreamCoachTurnBaseOptions &
  (
    | { acquiredLease: ChatTurnLeaseState; leaseId: string }
    | { acquiredLease?: undefined; leaseId?: string }
  );

// Seeding all the way to DEFAULT_AUTO_COMPACT_TOKENS makes a fresh session compact
// on its very first turn: trimBootstrapToBudget accepts a suffix at exactly the
// budget, and shouldTriggerCompaction fires at >= that same number. The seed is only
// the stored history — the lead-coach system prompt plus tool definitions add ~10.4k
// tokens on top of it before the athlete's message and any specialist reports, so a
// 60k seed lands the first request around 70k and trips the threshold immediately.
// Keep three quarters of the threshold so a cold seed has room for a turn.
const COLD_SEED_BUDGET_FRACTION = 0.75;
const COLD_SEED_TOKEN_BUDGET = Math.floor(
  DEFAULT_AUTO_COMPACT_TOKENS * COLD_SEED_BUDGET_FRACTION,
);
const PRE_RUN_FETCH_TIMEOUT_MS = 10_000;
export const CHAT_TURN_LEASE_RENEW_INTERVAL_MS = 20_000;

type PrepareDurableSessionOptions = {
  accessToken: string;
  baseUrl: string;
  context: AthleteContextBundle;
  extraHeaders?: Record<string, string>;
  leaseId: string;
  leaseState?: ChatTurnLeaseState;
  logger: Logger;
  markLeaseAcquired: () => void;
  selectedMessages: UIMessage[];
  signal?: AbortSignal;
};

type PreparedDurableSession = {
  durableSession: DurableCompactionSession;
  traceGroupId: string;
  underlyingSession: SupabaseAgentSession;
};

function generateUuid(): string {
  return crypto.randomUUID();
}

function prepareBootstrapItems(
  session: SupabaseAgentSession,
  messages: UIMessage[],
): AgentInputItem[] {
  return toAgentInputItems(messages).map((item) =>
    session.prepareHistoryItemForModelInput(item),
  );
}

function trimBootstrapToBudget(
  session: SupabaseAgentSession,
  messages: UIMessage[],
  logger: Logger,
): UIMessage[] {
  for (let start = 0; start < messages.length; start += 1) {
    const candidate = messages.slice(start);
    if (
      estimateStoredContext(prepareBootstrapItems(session, candidate))
        .estimatedTokens <= COLD_SEED_TOKEN_BUDGET
    ) {
      return candidate;
    }
  }
  // Every suffix exceeds the budget — seed the session cold rather than
  // exceeding the token limit.  Log so this case is visible in production.
  logger.warn(
    "coach: trimBootstrapToBudget: all messages exceed budget; session seeded cold",
    { messageCount: messages.length },
  );
  return [];
}

async function initializeSessionFromTranscript(options: {
  session: SupabaseAgentSession;
  accessToken: string;
  baseUrl: string;
  extraHeaders?: Record<string, string>;
  currentMessageIds: Set<string>;
  logger: Logger;
  signal?: AbortSignal;
}): Promise<void> {
  if ((await options.session.getItems()).length > 0) return;
  let before: string | null = null;
  let selected: UIMessage[] = [];
  do {
    const url = new URL(`${options.baseUrl}/api/chat/messages`);
    url.searchParams.set("limit", "100");
    if (before) url.searchParams.set("before", before);
    const response = await fetch(url, {
      headers: {
        Authorization: `Bearer ${options.accessToken}`,
        ...(options.extraHeaders ?? {}),
      },
      signal: fetchSignalWithTimeout(options.signal, PRE_RUN_FETCH_TIMEOUT_MS),
    });
    if (!response.ok)
      throw new Error(`Unable to initialize model state (${response.status})`);
    const page = (await response.json()) as {
      messages: UIMessage[];
      next_cursor: string | null;
    };
    const older = page.messages.filter(
      (message) => !options.currentMessageIds.has(message.id),
    );
    const candidate = [...older, ...selected];
    const candidateItems = prepareBootstrapItems(options.session, candidate);
    if (
      estimateStoredContext(candidateItems).estimatedTokens >
      COLD_SEED_TOKEN_BUDGET
    ) {
      selected = trimBootstrapToBudget(
        options.session,
        candidate,
        options.logger,
      );
      break;
    }
    selected = candidate;
    before = page.next_cursor;
  } while (before !== null);

  if (selected.length > 0) {
    await options.session.addItems(
      prepareBootstrapItems(options.session, selected),
    );
  }
}

/**
 * Returns true when the message contains at least one part with renderable
 * content (non-empty text, tool calls/results, files, etc.).  A message whose
 * only part is `step-start` — which happens when a turn fails before any model
 * output is produced — is treated as empty and must not be persisted.
 */
function hasRenderableContent(parts: UIMessage["parts"]): boolean {
  return parts.some((part) => {
    if (part.type === "step-start") return false;
    if (part.type === "text") return part.text.length > 0;
    return true; // tool-invocation, tool-result, dynamic-tool, file, reasoning, …
  });
}

async function persistAssistantMessage(
  options: Pick<
    StreamCoachTurnOptions,
    "accessToken" | "baseUrl" | "extraHeaders"
  > & { logger: Logger },
  responseMessage: UIMessage,
  finishReason: string | undefined,
): Promise<void> {
  if (!hasRenderableContent(responseMessage.parts)) return;
  try {
    const response = await fetch(`${options.baseUrl}/api/chat/messages`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${options.accessToken}`,
        "Content-Type": "application/json",
        ...(options.extraHeaders ?? {}),
      },
      body: JSON.stringify({
        id: responseMessage.id,
        role: "assistant",
        parts: responseMessage.parts,
        metadata: {
          message_kind: "assistant_reply",
          finish_reason: finishReason,
          client_message_id: responseMessage.id,
        },
      }),
    });
    if (!response.ok) {
      options.logger.error("coach: persist assistant reply failed", {
        status: response.status,
      });
    }
  } catch (error) {
    Sentry.captureException(error, {
      tags: { subsystem: "persist-assistant" },
    });
    options.logger.error("coach: persist assistant reply error", {
      error: error instanceof Error ? error.message : String(error),
    });
  }
}

async function acquireDurableLeaseState(
  options: PrepareDurableSessionOptions,
): Promise<{ thread_id?: string } | null> {
  try {
    return await acquireChatTurnLease({
      accessToken: options.accessToken,
      baseUrl: options.baseUrl,
      ...(options.extraHeaders ? { extraHeaders: options.extraHeaders } : {}),
      leaseId: options.leaseId,
      onLeaseAcquired: options.markLeaseAcquired,
      ...(options.signal ? { signal: options.signal } : {}),
      ttlSeconds: CHAT_TURN_LEASE_TTL_SECONDS,
    });
  } catch (leaseError) {
    if (
      options.signal?.aborted ||
      (leaseError instanceof LeaseAcquisitionError && leaseError.status === 409)
    ) {
      throw leaseError;
    }
    Sentry.captureException(leaseError, {
      tags: { subsystem: "lease-acquire", degrading: "true" },
    });
    options.logger.warn(
      "coach: lease fetch failed; degrading to stateless mode",
      {
        error:
          leaseError instanceof Error ? leaseError.message : String(leaseError),
      },
    );
    return null;
  }
}

function createUnderlyingSession(
  options: PrepareDurableSessionOptions,
): SupabaseAgentSession {
  return new SupabaseAgentSession({
    accessToken: options.accessToken,
    baseUrl: options.baseUrl,
    leaseId: options.leaseId,
    ...(options.signal ? { signal: options.signal } : {}),
    ...(options.extraHeaders ? { extraHeaders: options.extraHeaders } : {}),
  });
}

async function compactIfNeeded(
  durableSession: DurableCompactionSession,
  estimate: ReturnType<typeof estimateStoredContext>,
): Promise<void> {
  // Check the session's item-count trigger before every turn. Post-turn
  // compaction never runs when a model turn fails, so relying on it alone can
  // strand an overgrown session in a permanent fail/retry loop.
  await durableSession.runCompaction({
    ...(estimate.estimatedTokens >= DEFAULT_AUTO_COMPACT_TOKENS
      ? { force: true }
      : {}),
    compactionMode: "input",
  });
}

// Which setup step was in flight when a failure degraded the turn. Recorded on
// the Sentry event so "model state unreachable" and "compaction blew up" stay
// distinguishable instead of collapsing into one undifferentiated alert.
type DurableSetupStep = "seed" | "project" | "compact";

async function buildDurableSession(
  options: PrepareDurableSessionOptions,
  leaseState: ChatTurnLeaseState,
  progress: { step: DurableSetupStep },
): Promise<PreparedDurableSession> {
  const traceGroupId = leaseState.thread_id ?? options.context.profile.user_id;
  const underlyingSession = createUnderlyingSession(options);
  progress.step = "seed";
  await initializeSessionFromTranscript({
    session: underlyingSession,
    accessToken: options.accessToken,
    baseUrl: options.baseUrl,
    ...(options.extraHeaders ? { extraHeaders: options.extraHeaders } : {}),
    currentMessageIds: new Set(
      options.selectedMessages.map((message) => message.id),
    ),
    logger: options.logger,
    ...(options.signal ? { signal: options.signal } : {}),
  });
  progress.step = "project";
  const projected = [
    ...(await underlyingSession.getItems()),
    ...toAgentInputItems(options.selectedMessages),
  ];
  const estimate = estimateStoredContext(projected);
  progress.step = "compact";
  const durableSession = new DurableCompactionSession({ underlyingSession });
  await compactIfNeeded(durableSession, estimate);
  return { durableSession, traceGroupId, underlyingSession };
}

/**
 * A durable session is an optimisation, not a prerequisite for answering the
 * athlete, so every setup step gets the same policy lease acquisition already
 * has: log, tag, and run the turn statelessly (issue #408).  Before this, only
 * the lease step degraded and everything after it — model-state/transcript
 * fetches, the compaction client, forced compaction — killed the turn outright,
 * so a transient 5xx surfaced as "Coach is unavailable".
 *
 * Degrading leaves any acquired lease held; the caller's `finally` releases it,
 * and a renewal loop started by `markLeaseAcquired` simply runs against a
 * stateless turn.  The turn's items never reach `chat_model_states`, which is
 * the same trade lease degradation already makes — and it stays recoverable,
 * because stored state is left untouched for the next turn to seed or compact.
 */
async function prepareDurableSession(
  options: PrepareDurableSessionOptions,
): Promise<PreparedDurableSession | null> {
  const leaseState =
    options.leaseState ?? (await acquireDurableLeaseState(options));
  if (leaseState === null) return null;

  const progress: { step: DurableSetupStep } = { step: "seed" };
  try {
    return await buildDurableSession(options, leaseState, progress);
  } catch (error) {
    // An abort means the turn is being torn down — either the caller cancelled
    // or renewal lost the lease to another turn. Continuing statelessly would
    // keep working on a turn that no longer owns its session.
    if (options.signal?.aborted) throw error;
    // A 409 escaping a setup write (`addItems` while seeding, `replaceAll`
    // during forced compaction) means the CAS/lease check rejected it after
    // exhausting retries — another turn owns this chat's durable state. That is
    // the same condition acquireDurableLeaseState and the renewal loop already
    // treat as fatal, so it must not be degraded away here either.
    if (error instanceof ModelStateError && error.status === 409) throw error;
    Sentry.captureException(error, {
      tags: {
        subsystem: "durable-session-setup",
        degrading: "true",
        step: progress.step,
      },
    });
    options.logger.warn(
      "coach: durable session setup failed; degrading to stateless mode",
      {
        error: error instanceof Error ? error.message : String(error),
        step: progress.step,
      },
    );
    return null;
  }
}

function hasActivityFileAttachment(messages: UIMessage[]): boolean {
  return messages.some((message) =>
    message.parts.some((part) => {
      const file = nonImageFilePart(part);
      return (
        file !== null &&
        (isActivityFile(file.mediaType, file.filename) ||
          isZipUpload(file.mediaType, file.filename))
      );
    }),
  );
}

function latestUserTurnMessages(messages: UIMessage[]): UIMessage[] {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role === "user") {
      return [message];
    }
  }
  return [];
}

function createTavilyServer(url: string | undefined): MCPServer | null {
  if (url === undefined) return null;
  // No cacheToolsList: the tools are listed once per turn by listBudgetedMcpTools,
  // and the SDK's module-wide list cache is exactly what must not decide them.
  return new MCPServerStreamableHttp({ name: "tavily", url });
}

// Web search enriches a reply but is never the reply: a Tavily outage degrades the
// turn to one without its tools instead of failing it. Listing now happens every
// turn (no module-wide cache), so this guard covers more than connect alone.
async function connectTavilyTools(
  server: MCPServer,
  signal: AbortSignal,
  logger: Logger,
): Promise<Tool<CoachAgentRunContext>[]> {
  try {
    await server.connect();
    return await listBudgetedMcpTools<CoachAgentRunContext>(server);
  } catch (error) {
    if (signal.aborted) throw error;
    Sentry.captureException(error, {
      tags: { subsystem: "tavily-setup", degrading: "true" },
    });
    logger.warn("coach: Tavily setup failed; continuing without it", {
      error: error instanceof Error ? error.message : String(error),
    });
    return [];
  }
}

// Runs in the turn's finally block, where a throw would replace the turn's own
// outcome — including after a degraded setup that never connected.
async function closeTavilyServer(
  server: MCPServer,
  logger: Logger,
): Promise<void> {
  try {
    await server.close();
  } catch (error) {
    logger.warn("coach: Tavily close failed", {
      error: error instanceof Error ? error.message : String(error),
    });
  }
}

function startLeaseRenewal({
  accessToken,
  baseUrl,
  extraHeaders,
  leaseId,
  onLeaseLost,
}: Pick<
  PrepareDurableSessionOptions,
  "accessToken" | "baseUrl" | "extraHeaders" | "leaseId"
> & {
  onLeaseLost: (error: unknown) => void;
}): () => Promise<void> {
  // Renew requests always settle within RENEW_TIMEOUT_MS (5s), well under the
  // 20s tick interval, so ticks can't overlap in practice. We still track the
  // in-flight promise so stop() can await it before the caller releases the
  // lease, instead of letting a trailing renewal race the release call.
  let inFlight: Promise<void> | null = null;
  const intervalId = setInterval(() => {
    if (inFlight) return;
    inFlight = renewChatTurnLease({
      accessToken,
      baseUrl,
      ...(extraHeaders ? { extraHeaders } : {}),
      leaseId,
      ttlSeconds: CHAT_TURN_LEASE_TTL_SECONDS,
    })
      .catch((error) => {
        Sentry.captureException(error, {
          tags: { subsystem: "lease-renew" },
          extra: { leaseId },
        });
        // A 409 means another request has taken over this chat turn's
        // lease; keeping this turn running risks two turns writing the
        // same durable session concurrently, so abort it.
        if (error instanceof LeaseRenewalError && error.status === 409) {
          onLeaseLost(error);
        }
      })
      .finally(() => {
        inFlight = null;
      });
  }, CHAT_TURN_LEASE_RENEW_INTERVAL_MS);
  return async (): Promise<void> => {
    clearInterval(intervalId);
    await inFlight;
  };
}

// streamCoachTurn's options after defaults and model-message selection, carried
// through every step of the turn.
type CoachTurnRequest = {
  accessToken: string;
  acquiredLease: ChatTurnLeaseState | undefined;
  baseUrl: string;
  context: AthleteContextBundle;
  extraHeaders: Record<string, string> | undefined;
  logger: Logger;
  providedLeaseId: string | undefined;
  runContext: CoachAgentRunContext;
  selectedMessages: UIMessage[];
  signal: AbortSignal | undefined;
  streamErrorMessage: string;
  tavilyMcpUrl: string | undefined;
  timeZone: string | undefined;
  useDurableSession: boolean;
};

// Mutable per-turn state, shared by reference between the turn steps and the
// stream's error and cleanup paths. `stopLeaseRenewal` is assigned late by
// `markLeaseAcquired`, so cleanup must read it off this object, not a copy.
type TurnLifecycle = LeadTurnState & {
  leaseId: string;
  leaseStatus: { acquired: boolean; lost: boolean };
  onLeaseLost: (error: unknown) => void;
  stopLeaseRenewal: (() => Promise<void>) | undefined;
};

type TurnSession = {
  durableSession: DurableCompactionSession | undefined;
  traceGroupId: string;
  underlyingSession: SupabaseAgentSession | undefined;
};

type CoachTurn = {
  lifecycle: TurnLifecycle;
  request: CoachTurnRequest;
  session: TurnSession;
  writer: UIMessageStreamWriter;
};

function leaseRequest(
  request: CoachTurnRequest,
  leaseId: string,
): Pick<
  PrepareDurableSessionOptions,
  "accessToken" | "baseUrl" | "extraHeaders" | "leaseId"
> {
  return {
    accessToken: request.accessToken,
    baseUrl: request.baseUrl,
    ...(request.extraHeaders ? { extraHeaders: request.extraHeaders } : {}),
    leaseId,
  };
}

function startTurnLeaseRenewal(
  request: CoachTurnRequest,
  lifecycle: TurnLifecycle,
): () => Promise<void> {
  return startLeaseRenewal({
    ...leaseRequest(request, lifecycle.leaseId),
    onLeaseLost: lifecycle.onLeaseLost,
  });
}

function createTurnLifecycle(request: CoachTurnRequest): TurnLifecycle {
  const leaseId = request.providedLeaseId ?? crypto.randomUUID();
  // Lease renewal runs concurrently with the turn; if the lease is lost
  // (another request took it over), abort this turn via the same signal
  // already threaded through runner.run() rather than letting it keep
  // writing to a durable session it no longer owns.
  const leaseAbortController = new AbortController();
  const effectiveSignal = request.signal
    ? AbortSignal.any([request.signal, leaseAbortController.signal])
    : leaseAbortController.signal;
  const lifecycle: TurnLifecycle = {
    admittedProbes: new Set<ModelAdmission>(),
    effectiveSignal,
    // Indirection through a function, rather than reading
    // effectiveSignal.aborted directly at each call site, keeps
    // typescript-eslint from narrowing it to a stale literal across
    // `await`s — the signal can flip to aborted at any time.
    isTurnAborted: () => effectiveSignal.aborted,
    leaseId,
    leaseStatus: { acquired: request.acquiredLease !== undefined, lost: false },
    onLeaseLost: (error) => {
      lifecycle.leaseStatus.lost = true;
      leaseAbortController.abort(
        error instanceof Error ? error : new Error("chat turn lease lost"),
      );
    },
    rateLimits: createTurnRateLimitLedger(),
    stopLeaseRenewal: undefined,
    textState: { textId: "coach-response", textStarted: false },
  };
  if (request.acquiredLease !== undefined) {
    lifecycle.stopLeaseRenewal = startTurnLeaseRenewal(request, lifecycle);
  }
  return lifecycle;
}

function markLeaseAcquired(
  request: CoachTurnRequest,
  lifecycle: TurnLifecycle,
): void {
  lifecycle.leaseStatus.acquired = true;
  // Acquisition fires at most once per turn, but guard anyway: a second call
  // must not overwrite a live renewal's stop closure, which would orphan the
  // first interval so the turn-end stopLeaseRenewal?.() could never clear it.
  if (lifecycle.stopLeaseRenewal) return;
  lifecycle.stopLeaseRenewal = startTurnLeaseRenewal(request, lifecycle);
}

async function prepareTurnSession(
  request: CoachTurnRequest,
  lifecycle: TurnLifecycle,
): Promise<TurnSession> {
  const stateless: TurnSession = {
    durableSession: undefined,
    traceGroupId: request.context.profile.user_id,
    underlyingSession: undefined,
  };
  if (!request.useDurableSession) return stateless;
  const prepared = await prepareDurableSession({
    accessToken: request.accessToken,
    baseUrl: request.baseUrl,
    context: request.context,
    ...(request.extraHeaders ? { extraHeaders: request.extraHeaders } : {}),
    leaseId: lifecycle.leaseId,
    ...(request.acquiredLease ? { leaseState: request.acquiredLease } : {}),
    logger: request.logger,
    markLeaseAcquired: () => markLeaseAcquired(request, lifecycle),
    selectedMessages: request.selectedMessages,
    signal: lifecycle.effectiveSignal,
  });
  return prepared ?? stateless;
}

function admitStartingTier(lifecycle: TurnLifecycle): ModelAdmission {
  const admission = modelCircuitBreaker.pickTier(MODEL_TIERS);
  if (admission.probeGeneration !== undefined) {
    lifecycle.admittedProbes.add(admission);
  }
  return admission;
}

async function planDelegation(
  turn: CoachTurn,
  specialistTier: ModelTier,
  coachingMemory: CoachingMemoryRecord[],
): Promise<DelegationPlan> {
  const { underlyingSession } = turn.session;
  try {
    return await planSpecialistDelegation({
      durableContext: underlyingSession
        ? await underlyingSession.getItems()
        : [],
      latestUserTurn: toAgentInputItems(turn.request.selectedMessages),
      coachingMemory,
      athleteContext: turn.request.context,
      tier: specialistTier,
    });
  } catch (error) {
    if (isRateLimitError(error)) {
      turn.lifecycle.rateLimits.note(specialistTier, error);
      captureRateLimit({ tier: specialistTier, outcome: "exhausted", error });
    } else {
      Sentry.captureException(error, {
        tags: { subsystem: "delegation-planner" },
      });
    }
    turn.request.logger.warn("coach: delegation failed; continuing lead-only");
    return { delegations: [] };
  }
}

async function consultSpecialists(
  turn: CoachTurn,
  specialistTier: ModelTier,
  coachingMemory: CoachingMemoryRecord[],
  delegationPlan: DelegationPlan,
): Promise<SpecialistReport[]> {
  // runSpecialists() handles per-specialist failures (bad report, bad
  // proposedUpdate, execution error) internally so one malformed specialist
  // doesn't discard the others and always returns already-valid reports; this
  // catch is a last-resort net for anything unexpected outside that.
  try {
    return await runSpecialists({
      messages: turn.request.selectedMessages,
      messagesAreModelSelected: true,
      tier: specialistTier,
      roles: delegationPlan.delegations.map((item) => item.role),
      delegations: delegationPlan.delegations,
      coachingMemory,
      onRateLimit: turn.lifecycle.rateLimits.note,
      slices: buildContextSlices(turn.request.context),
      timeZone: turn.request.timeZone,
    });
  } catch (error) {
    Sentry.captureException(error, {
      tags: { subsystem: "specialists" },
    });
    turn.request.logger.warn(
      "coach: specialist run failed; continuing lead-only",
    );
    return [];
  }
}

/**
 * The `fitness-coach-turn` trace body: delegation planning, specialists, then
 * the lead coach down the model ladder. Turn-wide rate limits are flushed to
 * the circuit breaker and the Tavily MCP server closed however the ladder ends.
 */
async function runCoachTurn(
  turn: CoachTurn,
  startingAdmission: ModelAdmission,
): Promise<void> {
  const { lifecycle, request, session } = turn;
  const { underlyingSession } = session;
  const specialistTier = resolveSpecialistTier(startingAdmission.tier);
  const coachingMemory = underlyingSession
    ? await underlyingSession.getCoachingMemory()
    : [];
  const delegationPlan = await planDelegation(
    turn,
    specialistTier,
    coachingMemory,
  );
  const reports = await consultSpecialists(
    turn,
    specialistTier,
    coachingMemory,
    delegationPlan,
  );
  const durableFallbackBaseline = underlyingSession
    ? await underlyingSession.getItems()
    : undefined;
  const tavilyServer = createTavilyServer(request.tavilyMcpUrl);

  try {
    const tavilyTools =
      tavilyServer === null
        ? []
        : await connectTavilyTools(
            tavilyServer,
            lifecycle.effectiveSignal,
            request.logger,
          );
    await runLeadModelLadder(
      {
        accessToken: request.accessToken,
        baseUrl: request.baseUrl,
        coachingMemory,
        context: request.context,
        durableFallbackBaseline,
        durableSession: session.durableSession,
        extraHeaders: request.extraHeaders,
        lifecycle,
        reports,
        runContext: request.runContext,
        selectedMessages: request.selectedMessages,
        tavilyTools,
        timeZone: request.timeZone,
        traceGroupId: session.traceGroupId,
        underlyingSession,
        writer: turn.writer,
      },
      startingAdmission,
    );
  } finally {
    lifecycle.rateLimits.flushToBreaker(startingAdmission);
    if (tavilyServer !== null) {
      await closeTavilyServer(tavilyServer, request.logger);
    }
  }
}

async function traceCoachTurn(
  turn: CoachTurn,
  startingAdmission: ModelAdmission,
): Promise<void> {
  const startingTier = startingAdmission.tier;
  const traceMetadata = {
    fallbackModels: MODEL_TIERS.map((tier) => tier.model).join(","),
    startingModel: startingTier.model,
  } satisfies Record<string, string>;
  await Sentry.startSpan(
    {
      name: "fitness-coach-turn",
      // OpenTelemetry GenAI semantic conventions so Sentry/OTel backends
      // recognise this as an agent-invocation span.
      op: "gen_ai.invoke_agent",
      attributes: {
        "gen_ai.system": "openai",
        ...tierSpanAttributes(startingTier),
        "user.id": turn.request.context.profile.user_id,
      },
    },
    () =>
      withTrace(
        "fitness-coach-turn",
        () => runCoachTurn(turn, startingAdmission),
        {
          groupId: turn.session.traceGroupId,
          metadata: traceMetadata,
        },
      ),
  );
}

function finishTurnStream(
  request: CoachTurnRequest,
  writer: UIMessageStreamWriter,
  textState: StreamTextState,
): void {
  finishAgentText(writer, textState);
  request.logger.info("coach turn complete", {
    textStarted: textState.textStarted,
    user_id: request.context.profile.user_id,
  });
  writer.write({ type: "finish-step" });
  writer.write({ type: "finish", finishReason: "stop" });
}

function failTurnStream(
  request: CoachTurnRequest,
  writer: UIMessageStreamWriter,
  lifecycle: TurnLifecycle,
  error: unknown,
): void {
  if (request.signal?.aborted) {
    writer.write({ type: "abort", reason: "request aborted" });
    return;
  }
  const { textState } = lifecycle;
  const userErrorMessage = rateLimitStreamErrorMessage(
    error,
    request.streamErrorMessage,
    lifecycle.rateLimits.longestLeadRetryAfterMs(),
  );
  if (!textState.textStarted) {
    writeDeterministicText(writer, textState, userErrorMessage);
  }
  finishAgentText(writer, textState);
  writer.write({ type: "error", errorText: userErrorMessage });
  writer.write({ type: "finish-step" });
  writer.write({ type: "finish", finishReason: "error" });
  // The lease-lost case already reported to Sentry from onLeaseLost;
  // this `error` here is just the resulting abort, not new information.
  if (!lifecycle.leaseStatus.lost) {
    Sentry.captureException(error, {
      tags: { subsystem: "coach-stream" },
      extra: { textStarted: textState.textStarted },
    });
  }
  const message = error instanceof Error ? error.message : String(error);
  request.logger.error("coach: stream error", {
    error: message.replace(/key=[^&\s]+/g, "key=***"),
  });
}

async function cleanUpTurn(
  request: CoachTurnRequest,
  lifecycle: TurnLifecycle,
): Promise<void> {
  for (const admission of lifecycle.admittedProbes) {
    modelCircuitBreaker.releaseProbe(admission);
  }
  await lifecycle.stopLeaseRenewal?.();
  // If we lost the lease, we no longer own it — attempting to release
  // it would just fail with another 409 that only adds Sentry noise.
  if (!lifecycle.leaseStatus.acquired || lifecycle.leaseStatus.lost) return;
  try {
    await releaseChatTurnLease(leaseRequest(request, lifecycle.leaseId));
  } catch (error) {
    Sentry.captureException(error, {
      tags: { subsystem: "lease-release" },
      extra: { leaseId: lifecycle.leaseId },
    });
  }
}

// Lease, compaction, delegation, streaming, and cleanup form one turn
// transaction; each step below owns one phase of it.
async function executeCoachTurn(
  request: CoachTurnRequest,
  writer: UIMessageStreamWriter,
): Promise<void> {
  writer.write({ type: "start" });
  writer.write({ type: "start-step" });
  const lifecycle = createTurnLifecycle(request);
  try {
    const session = await prepareTurnSession(request, lifecycle);
    const startingAdmission = admitStartingTier(lifecycle);
    await traceCoachTurn(
      { lifecycle, request, session, writer },
      startingAdmission,
    );
    finishTurnStream(request, writer, lifecycle.textState);
  } catch (error) {
    failTurnStream(request, writer, lifecycle, error);
  } finally {
    await cleanUpTurn(request, lifecycle);
  }
}

export function streamCoachTurn({
  accessToken,
  acquiredLease,
  baseUrl,
  context,
  extraHeaders,
  leaseId: providedLeaseId,
  logger = createLogger(),
  messages,
  messagesAreModelSelected = false,
  signal,
  streamErrorMessage = "Coach is unavailable right now. Please try again.",
  tavilyMcpUrl,
  timeZone,
  useDurableSession = false,
}: StreamCoachTurnOptions): Response {
  const selectedMessages = messagesAreModelSelected
    ? messages
    : selectMessagesForModel(messages);
  const latestUserTurn = latestUserTurnMessages(selectedMessages);
  const request: CoachTurnRequest = {
    accessToken,
    acquiredLease,
    baseUrl,
    context,
    extraHeaders,
    logger,
    providedLeaseId,
    runContext: {
      hasActivityFileAttachment: hasActivityFileAttachment(latestUserTurn),
      toolCalled: false,
      toolRounds: new ToolRoundBudget(),
    },
    selectedMessages,
    signal,
    streamErrorMessage,
    tavilyMcpUrl,
    timeZone,
    useDurableSession,
  };

  const stream = createUIMessageStream<UIMessage>({
    generateId: generateUuid,
    originalMessages: selectedMessages,
    onError: () => streamErrorMessage,
    onFinish: async ({ finishReason, isAborted, responseMessage }) => {
      if (isAborted) return;
      await persistAssistantMessage(
        {
          accessToken,
          baseUrl,
          ...(extraHeaders ? { extraHeaders } : {}),
          logger,
        },
        responseMessage,
        finishReason,
      );
    },
    execute: ({ writer }) => executeCoachTurn(request, writer),
  });

  return createUIMessageStreamResponse({ stream });
}
