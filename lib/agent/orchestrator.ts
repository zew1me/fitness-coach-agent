import {
  Agent,
  MaxTurnsExceededError,
  MCPServerStreamableHttp,
  Runner,
  withTrace,
  type AgentInputItem,
  type MCPServer,
} from "@openai/agents";
import * as Sentry from "@sentry/nextjs";
import {
  createUIMessageStream,
  createUIMessageStreamResponse,
  type UIMessageStreamWriter,
  type UIMessage,
} from "ai";

import { toAgentInputItems } from "./agent-input";
import { finishAgentText, writeAgentStreamEvent } from "./agent-stream";
import {
  createAgentCoachTools,
  isActivityFile,
  isZipUpload,
  type CoachAgentRunContext,
} from "./coach-tools";
import { oldestDueFollowUp } from "./coaching-memory";
import { buildContextSlices } from "./context-slices";
import { planSpecialistDelegation } from "./delegation-planner";
import {
  DEFAULT_AUTO_COMPACT_TOKENS,
  DurableCompactionSession,
  estimateStoredContext,
} from "./durable-compaction-session";
import { fetchSignalWithTimeout } from "./fetch-signal";
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
import { modelCircuitBreaker } from "./model-circuit-breaker";
import {
  buildModelSettings,
  captureRateLimit,
  getRetryAfterMs,
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
import { buildLeadCoachPrompt } from "./system-prompt";
import type { AthleteContextBundle } from "./types";
import { recordStageUsage } from "./usage-metrics";

type StreamCoachTurnBaseOptions = {
  accessToken: string;
  baseUrl: string;
  context: AthleteContextBundle;
  extraHeaders?: Record<string, string>;
  messages: UIMessage[];
  messagesAreModelSelected?: boolean;
  signal?: AbortSignal;
  streamErrorMessage?: string;
  tavilyMcpUrl?: string;
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

const MAX_COACH_STEPS = 4;
const LAZY_SEED_TOKEN_BUDGET = 200_000;
const PRE_RUN_FETCH_TIMEOUT_MS = 10_000;
export const CHAT_TURN_LEASE_RENEW_INTERVAL_MS = 20_000;
const ACKNOWLEDGEMENT_PROMPT =
  "You just completed an action for the athlete. Use the prior tool result in the conversation to write a brief 1-2 sentence acknowledgement of what changed, then end with one short question or prompt to continue. Do not call tools. Be warm and concise.";
const EMPTY_MODEL_RESPONSE = "Hey, can you remind me of where we are at?";

function rateLimitStreamErrorMessage(
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

type PrepareDurableSessionOptions = {
  accessToken: string;
  baseUrl: string;
  context: AthleteContextBundle;
  extraHeaders?: Record<string, string>;
  leaseId: string;
  leaseState?: ChatTurnLeaseState;
  markLeaseAcquired: () => void;
  selectedMessages: UIMessage[];
  signal?: AbortSignal;
};

type PreparedDurableSession = {
  durableSession: DurableCompactionSession;
  traceGroupId: string;
  underlyingSession: SupabaseAgentSession;
};

type StreamTextState = {
  lastToolName?: string;
  textId: string;
  textStarted: boolean;
};

function generateUuid(): string {
  return crypto.randomUUID();
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

function writeDeterministicText(
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
): UIMessage[] {
  for (let start = 0; start < messages.length; start += 1) {
    const candidate = messages.slice(start);
    if (
      estimateStoredContext(prepareBootstrapItems(session, candidate))
        .estimatedTokens <= LAZY_SEED_TOKEN_BUDGET
    ) {
      return candidate;
    }
  }
  // Every suffix exceeds the budget — seed the session cold rather than
  // exceeding the token limit.  Log so this case is visible in production.
  Sentry.logger.warn(
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
      LAZY_SEED_TOKEN_BUDGET
    ) {
      selected = trimBootstrapToBudget(options.session, candidate);
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
  >,
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
      Sentry.logger.error("coach: persist assistant reply failed", {
        status: response.status,
      });
      console.error("[chat] persist assistant reply failed:", response.status);
    }
  } catch (error) {
    Sentry.captureException(error, {
      tags: { subsystem: "persist-assistant" },
    });
    const message = error instanceof Error ? error.message : String(error);
    console.error("[chat] persist assistant reply error:", message);
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
    Sentry.logger.warn(
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
    Sentry.logger.warn(
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
  return new MCPServerStreamableHttp({
    cacheToolsList: true,
    name: "tavily",
    url,
  });
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

export function streamCoachTurn({
  accessToken,
  acquiredLease,
  baseUrl,
  context,
  extraHeaders,
  leaseId: providedLeaseId,
  messages,
  messagesAreModelSelected = false,
  signal,
  streamErrorMessage = "Coach is unavailable right now. Please try again.",
  tavilyMcpUrl,
  useDurableSession = false,
}: StreamCoachTurnOptions): Response {
  const selectedMessages = messagesAreModelSelected
    ? messages
    : selectMessagesForModel(messages);
  const latestUserTurn = latestUserTurnMessages(selectedMessages);
  const runContext: CoachAgentRunContext = {
    hasActivityFileAttachment: hasActivityFileAttachment(latestUserTurn),
    toolCalled: false,
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
        },
        responseMessage,
        finishReason,
      );
    },
    // Lease, compaction, delegation, streaming, and cleanup form one turn transaction.
    // eslint-disable-next-line complexity
    execute: async ({ writer }) => {
      writer.write({ type: "start" });
      writer.write({ type: "start-step" });
      const textState: StreamTextState = {
        textId: "coach-response",
        textStarted: false,
      };

      const leaseId = providedLeaseId ?? crypto.randomUUID();
      const leaseStatus = {
        acquired: acquiredLease !== undefined,
        lost: false,
      };
      // Lease renewal runs concurrently with the turn; if the lease is lost
      // (another request took it over), abort this turn via the same signal
      // already threaded through runner.run() rather than letting it keep
      // writing to a durable session it no longer owns.
      const leaseAbortController = new AbortController();
      const effectiveSignal = signal
        ? AbortSignal.any([signal, leaseAbortController.signal])
        : leaseAbortController.signal;
      // Indirection through a function, rather than reading
      // effectiveSignal.aborted directly at each call site, keeps
      // typescript-eslint from narrowing it to a stale literal across the
      // `await`s below — the signal can flip to aborted at any time.
      const isTurnAborted = (): boolean => effectiveSignal.aborted;
      const onLeaseLost = (error: unknown): void => {
        leaseStatus.lost = true;
        leaseAbortController.abort(
          error instanceof Error ? error : new Error("chat turn lease lost"),
        );
      };
      const admittedProbes = new Set<
        ReturnType<typeof modelCircuitBreaker.pickTier>
      >();
      let maxLeadRetryAfterMs: number | undefined;
      let stopLeaseRenewal: (() => Promise<void>) | undefined =
        acquiredLease === undefined
          ? undefined
          : startLeaseRenewal({
              accessToken,
              baseUrl,
              ...(extraHeaders ? { extraHeaders } : {}),
              leaseId,
              onLeaseLost,
            });
      try {
        let durableSession: DurableCompactionSession | undefined;
        let underlyingSession: SupabaseAgentSession | undefined;
        let traceGroupId = context.profile.user_id;
        if (useDurableSession) {
          const prepared = await prepareDurableSession({
            accessToken,
            baseUrl,
            context,
            ...(extraHeaders ? { extraHeaders } : {}),
            leaseId,
            ...(acquiredLease ? { leaseState: acquiredLease } : {}),
            markLeaseAcquired: () => {
              leaseStatus.acquired = true;
              // Acquisition fires at most once per turn, but guard anyway: a
              // second call must not overwrite a live renewal's stop closure,
              // which would orphan the first interval so the turn-end
              // stopLeaseRenewal?.() could never clear it.
              if (stopLeaseRenewal) return;
              stopLeaseRenewal = startLeaseRenewal({
                accessToken,
                baseUrl,
                ...(extraHeaders ? { extraHeaders } : {}),
                leaseId,
                onLeaseLost,
              });
            },
            selectedMessages,
            signal: effectiveSignal,
          });
          if (prepared !== null) {
            durableSession = prepared.durableSession;
            underlyingSession = prepared.underlyingSession;
            traceGroupId = prepared.traceGroupId;
          }
        }
        const startingAdmission = modelCircuitBreaker.pickTier(MODEL_TIERS);
        const startingTier = startingAdmission.tier;
        const startingTierIndex = MODEL_TIERS.indexOf(startingTier);
        if (startingAdmission.probeGeneration !== undefined) {
          admittedProbes.add(startingAdmission);
        }
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
              "gen_ai.request.model": startingTier.model,
              "gen_ai.request.reasoning_effort": String(startingTier.effort),
              "coach.model_tier": startingTierIndex + 1,
              "coach.breaker_state": modelCircuitBreaker.snapshot(
                startingTier.model,
              ).state,
              "user.id": context.profile.user_id,
            },
          },
          () =>
            withTrace(
              "fitness-coach-turn",
              // The trace callback owns the complete model orchestration lifecycle.
              // eslint-disable-next-line complexity
              async () => {
                let delegationPlan: DelegationPlan = { delegations: [] };
                const turnRateLimits = new Map<
                  string,
                  { error: unknown; retryAfterMs?: number; tier: ModelTier }
                >();
                const breakerRecordedModels = new Set<string>();
                const noteTurnRateLimit = (
                  tier: ModelTier,
                  error: unknown,
                ): void => {
                  const retryAfterMs = getRetryAfterMs(error);
                  const existing = turnRateLimits.get(tier.model);
                  if (
                    existing &&
                    (existing.retryAfterMs ?? 0) >= (retryAfterMs ?? 0)
                  ) {
                    return;
                  }
                  turnRateLimits.set(tier.model, {
                    tier,
                    error,
                    ...(retryAfterMs === undefined ? {} : { retryAfterMs }),
                  });
                };
                const specialistTier = resolveSpecialistTier(startingTier);
                const coachingMemory = underlyingSession
                  ? await underlyingSession.getCoachingMemory()
                  : [];
                try {
                  delegationPlan = await planSpecialistDelegation({
                    durableContext: underlyingSession
                      ? await underlyingSession.getItems()
                      : [],
                    latestUserTurn: toAgentInputItems(selectedMessages),
                    coachingMemory,
                    athleteContext: context,
                    tier: specialistTier,
                  });
                } catch (error) {
                  if (isRateLimitError(error)) {
                    noteTurnRateLimit(specialistTier, error);
                    captureRateLimit({
                      tier: specialistTier,
                      outcome: "exhausted",
                      error,
                    });
                  } else {
                    Sentry.captureException(error, {
                      tags: { subsystem: "delegation-planner" },
                    });
                  }
                  Sentry.logger.warn(
                    "coach: delegation failed; continuing lead-only",
                  );
                }
                // runSpecialists() handles per-specialist failures (bad
                // report, bad proposedUpdate, execution error) internally so
                // one malformed specialist doesn't discard the others and
                // always returns already-valid reports; this catch is a
                // last-resort net for anything unexpected outside that.
                let reports: SpecialistReport[] = [];
                try {
                  reports = await runSpecialists({
                    messages: selectedMessages,
                    messagesAreModelSelected: true,
                    tier: specialistTier,
                    roles: delegationPlan.delegations.map((item) => item.role),
                    delegations: delegationPlan.delegations,
                    coachingMemory,
                    onRateLimit: noteTurnRateLimit,
                    slices: buildContextSlices(context),
                  });
                } catch (error) {
                  Sentry.captureException(error, {
                    tags: { subsystem: "specialists" },
                  });
                  Sentry.logger.warn(
                    "coach: specialist run failed; continuing lead-only",
                  );
                }
                const durableFallbackBaseline = underlyingSession
                  ? await underlyingSession.getItems()
                  : undefined;
                const tavilyServer = createTavilyServer(tavilyMcpUrl);

                try {
                  if (tavilyServer !== null) await tavilyServer.connect();
                  let admission = startingAdmission;
                  let tier: ModelTier = startingTier;
                  let tierIndex = startingTierIndex;
                  for (;;) {
                    const attemptProgress = { eventStarted: false };
                    try {
                      await withTrace(
                        "fitness-coach-tier-attempt",
                        // A tier attempt deliberately keeps streaming, tool acknowledgement,
                        // telemetry, and deterministic degradation in one trace boundary.
                        // eslint-disable-next-line complexity
                        async () => {
                          const lead = new Agent<CoachAgentRunContext>({
                            name: "Lead coach",
                            instructions: buildLeadCoachPrompt(
                              context,
                              reports,
                              oldestDueFollowUp(coachingMemory)?.statement,
                            ),
                            model: tier.model,
                            modelSettings: buildModelSettings(tier),
                            mcpServers:
                              tavilyServer === null ? [] : [tavilyServer],
                            tools: createAgentCoachTools({
                              accessToken,
                              baseUrl,
                              ...(extraHeaders ? { extraHeaders } : {}),
                              ...(underlyingSession
                                ? { modelSession: underlyingSession }
                                : {}),
                            }),
                          });
                          lead.on("agent_tool_start", (activeContext) => {
                            activeContext.context.toolCalled = true;
                          });

                          const runner = new Runner({
                            traceIncludeSensitiveData: true,
                            tracingDisabled: false,
                            workflowName: "fitness-coach-turn",
                          });
                          const result = await runner.run(
                            lead,
                            toAgentInputItems(selectedMessages),
                            {
                              context: runContext,
                              maxTurns: MAX_COACH_STEPS,
                              signal: effectiveSignal,
                              ...(durableSession
                                ? { session: durableSession }
                                : {}),
                              stream: true,
                            },
                          );

                          // The runner throws MaxTurnsExceededError only after
                          // this turn's tool calls have completed. Preserve that
                          // completed work by falling through to the
                          // acknowledgement/deterministic response below.
                          try {
                            for await (const event of result) {
                              attemptProgress.eventStarted = true;
                              writeAgentStreamEvent(event, writer, textState);
                            }
                            await result.completed;
                          } catch (error) {
                            if (!(error instanceof MaxTurnsExceededError)) {
                              throw error;
                            }
                            if (!isTurnAborted()) {
                              Sentry.captureException(error, {
                                tags: { subsystem: "coach-max-turns" },
                              });
                              Sentry.logger.warn(
                                "coach: max turns exceeded; salvaging completed tool work",
                                { max_turns: MAX_COACH_STEPS },
                              );
                            }
                          }
                          recordStageUsage("lead", result.state.usage);

                          const ranTool =
                            runContext.toolCalled ||
                            textState.lastToolName !== undefined;
                          let acknowledgementError: unknown;
                          if (
                            ranTool &&
                            !textState.textStarted &&
                            !isTurnAborted()
                          ) {
                            try {
                              const acknowledgement =
                                new Agent<CoachAgentRunContext>({
                                  name: "Coach acknowledgement",
                                  instructions: ACKNOWLEDGEMENT_PROMPT,
                                  model: tier.model,
                                  modelSettings: buildModelSettings(tier),
                                  mcpServers: [],
                                  tools: [],
                                });
                              const followup = await runner.run(
                                acknowledgement,
                                result.output,
                                {
                                  context: runContext,
                                  maxTurns: 2,
                                  signal: effectiveSignal,
                                  stream: true,
                                },
                              );
                              for await (const event of followup) {
                                writeAgentStreamEvent(event, writer, textState);
                              }
                              await followup.completed;
                              recordStageUsage(
                                "lead-followup",
                                followup.state.usage,
                              );
                            } catch (error) {
                              acknowledgementError = error;
                              if (!isTurnAborted()) {
                                if (isRateLimitError(error)) {
                                  noteTurnRateLimit(tier, error);
                                  if (!breakerRecordedModels.has(tier.model)) {
                                    modelCircuitBreaker.recordFailure(
                                      admission,
                                      getRetryAfterMs(error),
                                    );
                                    breakerRecordedModels.add(tier.model);
                                  }
                                  captureRateLimit({
                                    tier,
                                    outcome: "exhausted",
                                    error,
                                    textStarted: textState.textStarted,
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
                            }
                          }

                          if (!textState.textStarted && !isTurnAborted()) {
                            writeDeterministicText(
                              writer,
                              textState,
                              ranTool
                                ? buildRateLimitedToolAcknowledgement(
                                    textState.lastToolName,
                                    acknowledgementError,
                                  )
                                : EMPTY_MODEL_RESPONSE,
                            );
                          }
                        },
                        {
                          groupId: traceGroupId,
                          metadata: { model: tier.model },
                        },
                      );
                      if (isTurnAborted()) {
                        modelCircuitBreaker.releaseProbe(admission);
                        throw effectiveSignal.reason instanceof Error
                          ? effectiveSignal.reason
                          : new Error("chat turn aborted");
                      }
                      const auxiliaryRateLimit = turnRateLimits.get(tier.model);
                      if (
                        auxiliaryRateLimit &&
                        !breakerRecordedModels.has(tier.model)
                      ) {
                        modelCircuitBreaker.recordFailure(
                          admission,
                          auxiliaryRateLimit.retryAfterMs,
                        );
                        breakerRecordedModels.add(tier.model);
                      } else if (!auxiliaryRateLimit) {
                        modelCircuitBreaker.recordSuccess(admission);
                      }
                      break;
                    } catch (error) {
                      if (!isRateLimitError(error)) {
                        modelCircuitBreaker.releaseProbe(admission);
                        throw error;
                      }

                      const retryAfterMs = getRetryAfterMs(error);
                      if (
                        retryAfterMs !== undefined &&
                        (maxLeadRetryAfterMs === undefined ||
                          retryAfterMs > maxLeadRetryAfterMs)
                      ) {
                        maxLeadRetryAfterMs = retryAfterMs;
                      }
                      noteTurnRateLimit(tier, error);
                      if (!breakerRecordedModels.has(tier.model)) {
                        modelCircuitBreaker.recordFailure(
                          admission,
                          retryAfterMs,
                        );
                        breakerRecordedModels.add(tier.model);
                      }
                      if (
                        !textState.textStarted &&
                        (runContext.toolCalled ||
                          textState.lastToolName !== undefined) &&
                        !isTurnAborted()
                      ) {
                        captureRateLimit({
                          tier,
                          outcome: "exhausted",
                          error,
                          textStarted: false,
                        });
                        writeDeterministicText(
                          writer,
                          textState,
                          buildRateLimitedToolAcknowledgement(
                            textState.lastToolName,
                            error,
                          ),
                        );
                        break;
                      }
                      const canFallback =
                        !attemptProgress.eventStarted &&
                        !textState.textStarted &&
                        !runContext.toolCalled &&
                        textState.lastToolName === undefined &&
                        !isTurnAborted() &&
                        tierIndex + 1 < MODEL_TIERS.length;
                      if (!canFallback) {
                        captureRateLimit({
                          tier,
                          outcome: "exhausted",
                          error,
                          textStarted: textState.textStarted,
                        });
                        throw error;
                      }

                      if (durableFallbackBaseline && underlyingSession) {
                        await underlyingSession.replaceAll(
                          durableFallbackBaseline,
                          { trigger: "model-tier-fallback-rollback" },
                        );
                      }
                      const failedTierIndex = tierIndex;
                      const nextAdmission = modelCircuitBreaker.pickTier(
                        MODEL_TIERS.slice(tierIndex + 1),
                      );
                      const nextTier = nextAdmission.tier;
                      captureRateLimit({
                        tier,
                        outcome: "falling_back",
                        error,
                        fallbackModel: nextTier.model,
                        textStarted: textState.textStarted,
                      });
                      admission = nextAdmission;
                      tier = nextTier;
                      tierIndex = MODEL_TIERS.indexOf(nextTier);
                      if (admission.probeGeneration !== undefined) {
                        admittedProbes.add(admission);
                      }
                      Sentry.getActiveSpan()?.setAttribute(
                        "gen_ai.request.model",
                        tier.model,
                      );
                      Sentry.getActiveSpan()?.setAttribute(
                        "gen_ai.request.reasoning_effort",
                        String(tier.effort),
                      );
                      Sentry.getActiveSpan()?.setAttribute(
                        "coach.model_tier",
                        tierIndex + 1,
                      );
                      Sentry.getActiveSpan()?.setAttribute(
                        "coach.breaker_state",
                        modelCircuitBreaker.snapshot(tier.model).state,
                      );
                      Sentry.logger.warn(
                        `coach: tier ${failedTierIndex + 1} rate-limited; falling back to ${tier.model}`,
                      );
                    }
                  }
                } finally {
                  for (const rateLimit of turnRateLimits.values()) {
                    if (breakerRecordedModels.has(rateLimit.tier.model)) {
                      continue;
                    }
                    modelCircuitBreaker.recordFailure(
                      rateLimit.tier.model === startingTier.model
                        ? startingAdmission
                        : { tier: rateLimit.tier },
                      rateLimit.retryAfterMs,
                    );
                    breakerRecordedModels.add(rateLimit.tier.model);
                  }
                  if (tavilyServer !== null) await tavilyServer.close();
                }
              },
              {
                groupId: traceGroupId,
                metadata: traceMetadata,
              },
            ),
        );
        finishAgentText(writer, textState);
        Sentry.logger.info("coach turn complete", {
          textStarted: textState.textStarted,
          user_id: context.profile.user_id,
        });
        writer.write({ type: "finish-step" });
        writer.write({ type: "finish", finishReason: "stop" });
      } catch (error) {
        if (signal?.aborted) {
          writer.write({ type: "abort", reason: "request aborted" });
          return;
        }
        const userErrorMessage = rateLimitStreamErrorMessage(
          error,
          streamErrorMessage,
          maxLeadRetryAfterMs,
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
        if (!leaseStatus.lost) {
          Sentry.captureException(error, {
            tags: { subsystem: "coach-stream" },
            extra: { textStarted: textState.textStarted },
          });
        }
        const message = error instanceof Error ? error.message : String(error);
        console.error(
          "[chat] stream error:",
          message.replace(/key=[^&\s]+/g, "key=***"),
        );
      } finally {
        for (const admission of admittedProbes) {
          modelCircuitBreaker.releaseProbe(admission);
        }
        await stopLeaseRenewal?.();
        // If we lost the lease, we no longer own it — attempting to release
        // it would just fail with another 409 that only adds Sentry noise.
        if (leaseStatus.acquired && !leaseStatus.lost) {
          try {
            await releaseChatTurnLease({
              accessToken,
              baseUrl,
              ...(extraHeaders ? { extraHeaders } : {}),
              leaseId,
            });
          } catch (error) {
            Sentry.captureException(error, {
              tags: { subsystem: "lease-release" },
              extra: { leaseId },
            });
          }
        }
      }
    },
  });

  return createUIMessageStreamResponse({ stream });
}
