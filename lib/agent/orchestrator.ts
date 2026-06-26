import {
  Agent,
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
  type UIMessage,
} from "ai";

import { toAgentInputItems } from "./agent-input";
import { finishAgentText, writeAgentStreamEvent } from "./agent-stream";
import {
  createAgentCoachTools,
  type CoachAgentRunContext,
} from "./coach-tools";
import { oldestDueFollowUp } from "./coaching-memory";
import { buildContextSlices } from "./context-slices";
import { planSpecialistDelegation } from "./delegation-planner";
import { fetchSignalWithTimeout } from "./fetch-signal";
import { releaseChatTurnLease } from "./lease-client";
import { selectMessagesForModel } from "./message-context";
import {
  specialistReportsSchema,
  type DelegationPlan,
  type SpecialistReport,
} from "./orchestration-types";
import { runSpecialists } from "./specialists";
import {
  DurableCompactionSession,
  SupabaseAgentSession,
  estimateStoredContext,
} from "./supabase-agent-session";
import { buildLeadCoachPrompt } from "./system-prompt";
import type { AthleteContextBundle } from "./types";
import { recordStageUsage } from "./usage-metrics";

export type StreamCoachTurnOptions = {
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

const MAX_COACH_STEPS = 4;
const MODEL = "gpt-5.4-mini";
const LAZY_SEED_TOKEN_BUDGET = 200_000;
const PRE_RUN_FETCH_TIMEOUT_MS = 10_000;

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

function createTavilyServer(url: string | undefined): MCPServer | null {
  if (url === undefined) return null;
  return new MCPServerStreamableHttp({
    cacheToolsList: true,
    name: "tavily",
    url,
  });
}

export function streamCoachTurn({
  accessToken,
  baseUrl,
  context,
  extraHeaders,
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
  const runContext: CoachAgentRunContext = { toolCalled: false };

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
      const textState = { textId: "coach-response", textStarted: false };

      const leaseId = crypto.randomUUID();
      let leaseAcquired = false;
      try {
        let durableSession: DurableCompactionSession | undefined;
        let underlyingSession: SupabaseAgentSession | undefined;
        let traceGroupId = context.profile.user_id;
        if (useDurableSession) {
          // Attempt to acquire the durable-session lease.  A 409 means another
          // turn is genuinely in flight — fail fast.  Any other infra error
          // (missing table, 500, 503, network timeout) degrades gracefully to
          // stateless mode so the user always gets a response.
          let leaseObtained = false;
          let leaseState: { thread_id?: string } | undefined;
          try {
            const leaseResponse = await fetch(
              `${baseUrl}/api/chat/model-state/lease`,
              {
                method: "POST",
                headers: {
                  Authorization: `Bearer ${accessToken}`,
                  "Content-Type": "application/json",
                  ...(extraHeaders ?? {}),
                },
                body: JSON.stringify({ lease_id: leaseId, ttl_seconds: 300 }),
                signal: fetchSignalWithTimeout(
                  signal,
                  PRE_RUN_FETCH_TIMEOUT_MS,
                ),
              },
            );
            if (leaseResponse.status === 409) {
              throw new Error(
                `Unable to acquire chat turn lease (${leaseResponse.status})`,
              );
            }
            if (leaseResponse.ok) {
              leaseAcquired = true;
              leaseObtained = true;
              leaseState = (await leaseResponse.json()) as {
                thread_id?: string;
              };
            } else {
              Sentry.logger.warn(
                "coach: lease infra error; degrading to stateless mode",
                { status: leaseResponse.status },
              );
            }
          } catch (leaseError) {
            // Propagate abort and explicit conflict errors; swallow everything
            // else so the turn can still run without durable state.
            if (signal?.aborted) throw leaseError;
            const leaseMsg =
              leaseError instanceof Error
                ? leaseError.message
                : String(leaseError);
            if (leaseMsg.startsWith("Unable to acquire")) throw leaseError;
            Sentry.captureException(leaseError, {
              tags: { subsystem: "lease-acquire", degrading: "true" },
            });
            Sentry.logger.warn(
              "coach: lease fetch failed; degrading to stateless mode",
              { error: leaseMsg },
            );
          }
          if (leaseObtained && leaseState !== undefined) {
            traceGroupId = leaseState.thread_id ?? traceGroupId;
            underlyingSession = new SupabaseAgentSession({
              accessToken,
              baseUrl,
              leaseId,
              ...(signal ? { signal } : {}),
              ...(extraHeaders ? { extraHeaders } : {}),
            });
            await initializeSessionFromTranscript({
              session: underlyingSession,
              accessToken,
              baseUrl,
              ...(extraHeaders ? { extraHeaders } : {}),
              currentMessageIds: new Set(
                selectedMessages.map((message) => message.id),
              ),
              ...(signal ? { signal } : {}),
            });
            const projected = [
              ...(await underlyingSession.getItems()),
              ...toAgentInputItems(selectedMessages),
            ];
            const estimate = estimateStoredContext(projected);
            durableSession = new DurableCompactionSession({
              underlyingSession,
            });
            if (estimate.estimatedTokens >= 220_000) {
              try {
                await durableSession.runCompaction({
                  force: true,
                  compactionMode: "input",
                });
              } catch (error) {
                if (estimate.estimatedTokens >= 260_000) throw error;
                Sentry.captureException(error, {
                  tags: { subsystem: "forced-compaction" },
                  extra: { estimated_tokens: estimate.estimatedTokens },
                });
                Sentry.logger.warn(
                  "coach: forced compaction failed below hard limit",
                  {
                    estimated_tokens: estimate.estimatedTokens,
                    error:
                      error instanceof Error ? error.message : String(error),
                  },
                );
              }
            }
          }
        }
        await Sentry.startSpan(
          {
            name: "fitness-coach-turn",
            // OpenTelemetry GenAI semantic conventions so Sentry/OTel backends
            // recognise this as an agent-invocation span.
            op: "gen_ai.invoke_agent",
            attributes: {
              "gen_ai.system": "openai",
              "gen_ai.request.model": MODEL,
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
                    model: MODEL,
                  });
                } catch (error) {
                  Sentry.captureException(error, {
                    tags: { subsystem: "delegation-planner" },
                  });
                  Sentry.logger.warn(
                    "coach: delegation failed; continuing lead-only",
                  );
                }
                const reports = specialistReportsSchema.parse(
                  await runSpecialists({
                    messages: selectedMessages,
                    messagesAreModelSelected: true,
                    model: MODEL,
                    roles: delegationPlan.delegations.map((item) => item.role),
                    delegations: delegationPlan.delegations,
                    coachingMemory,
                    slices: buildContextSlices(context),
                  }),
                ) as SpecialistReport[];
                const tavilyServer = createTavilyServer(tavilyMcpUrl);
                if (tavilyServer !== null) await tavilyServer.connect();

                try {
                  const lead = new Agent<CoachAgentRunContext>({
                    name: "Lead coach",
                    instructions: buildLeadCoachPrompt(
                      context,
                      reports,
                      oldestDueFollowUp(coachingMemory)?.statement,
                    ),
                    model: MODEL,
                    mcpServers: tavilyServer === null ? [] : [tavilyServer],
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
                      ...(signal ? { signal } : {}),
                      ...(durableSession ? { session: durableSession } : {}),
                      stream: true,
                    },
                  );

                  for await (const event of result) {
                    writeAgentStreamEvent(event, writer, textState);
                  }
                  await result.completed;
                  recordStageUsage("lead", result.state.usage);
                } finally {
                  if (tavilyServer !== null) await tavilyServer.close();
                }
              },
              {
                groupId: traceGroupId,
                metadata: { model: MODEL },
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
        finishAgentText(writer, textState);
        writer.write({ type: "error", errorText: streamErrorMessage });
        writer.write({ type: "finish-step" });
        writer.write({ type: "finish", finishReason: "error" });
        Sentry.captureException(error, {
          tags: { subsystem: "coach-stream" },
          extra: { textStarted: textState.textStarted },
        });
        const message = error instanceof Error ? error.message : String(error);
        console.error(
          "[chat] stream error:",
          message.replace(/key=[^&\s]+/g, "key=***"),
        );
      } finally {
        if (leaseAcquired) {
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
