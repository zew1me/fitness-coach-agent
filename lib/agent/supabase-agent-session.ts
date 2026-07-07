// CRUD session against /api/chat/model-state — no knowledge of
// OpenAI.responses.compact. Compaction lives in durable-compaction-session.ts;
// see docs/COMPACTION_DESIGN.md.
import type {
  AgentInputItem,
  SessionHistoryRewriteArgs,
  SessionHistoryRewriteAwareSession,
} from "@openai/agents";

import { modelStateSchema, type ModelState } from "../schemas";

import {
  applyMemoryOperation,
  coachingMemoryRecordSchema,
  type CoachingMemoryOperation,
  type CoachingMemoryRecord,
} from "./coaching-memory";
import { fetchSignalWithTimeout } from "./fetch-signal";
import {
  prepareFunctionItemForModelInput,
  unsupportedFileContentToText,
} from "./responses-item-shapes";

const MODEL_STATE_FETCH_TIMEOUT_MS = 10_000;

type SessionOptions = {
  accessToken: string;
  baseUrl: string;
  extraHeaders?: Record<string, string>;
  fetch?: typeof fetch;
  leaseId: string;
  maxCasRetries?: number;
  signal?: AbortSignal;
};

export class SupabaseAgentSession implements SessionHistoryRewriteAwareSession {
  private readonly options: SessionOptions;
  private state: ModelState | null = null;
  private casRetries = 0;

  constructor(options: SessionOptions) {
    this.options = options;
  }

  private headers(): HeadersInit {
    return {
      Authorization: `Bearer ${this.options.accessToken}`,
      "Content-Type": "application/json",
      ...(this.options.extraHeaders ?? {}),
    };
  }

  private async load(force = false): Promise<ModelState> {
    if (this.state !== null && !force) return this.state;
    const response = await (this.options.fetch ?? fetch)(
      `${this.options.baseUrl}/api/chat/model-state`,
      {
        headers: this.headers(),
        signal: fetchSignalWithTimeout(
          this.options.signal,
          MODEL_STATE_FETCH_TIMEOUT_MS,
        ),
      },
    );
    if (!response.ok)
      throw new Error(`Unable to load model state (${response.status})`);
    this.state = modelStateSchema.parse(await response.json());
    return this.state;
  }

  private async mutate(
    mutation: (
      state: ModelState,
    ) => Pick<ModelState, "items" | "coaching_memory" | "compaction_metadata">,
  ): Promise<void> {
    const attempts = (this.options.maxCasRetries ?? 3) + 1;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      const current = await this.load(attempt > 0);
      const next = mutation(current);
      const response = await (this.options.fetch ?? fetch)(
        `${this.options.baseUrl}/api/chat/model-state`,
        {
          method: "PUT",
          headers: this.headers(),
          signal: fetchSignalWithTimeout(
            this.options.signal,
            MODEL_STATE_FETCH_TIMEOUT_MS,
          ),
          body: JSON.stringify({
            expected_version: current.version,
            lease_id: this.options.leaseId,
            ...next,
          }),
        },
      );
      if (response.ok) {
        this.casRetries = attempt;
        this.state = modelStateSchema.parse(await response.json());
        return;
      }
      if (response.status !== 409 || attempt === attempts - 1) {
        throw new Error(`Unable to replace model state (${response.status})`);
      }
    }
  }

  async getSessionId(): Promise<string> {
    return (await this.load()).thread_id;
  }

  getLastCasRetries(): number {
    return this.casRetries;
  }

  async getItems(limit?: number): Promise<AgentInputItem[]> {
    const items = (await this.load()).items;
    if (limit !== undefined && limit <= 0) return [];
    return limit === undefined ? [...items] : items.slice(-limit);
  }

  async getCoachingMemory(): Promise<CoachingMemoryRecord[]> {
    return (await this.load()).coaching_memory.map((record) =>
      coachingMemoryRecordSchema.parse(record),
    );
  }

  async updateCoachingMemory(
    operation: CoachingMemoryOperation,
  ): Promise<void> {
    await this.mutate((state) => ({
      items: state.items,
      coaching_memory: applyMemoryOperation(
        state.coaching_memory.map((record) =>
          coachingMemoryRecordSchema.parse(record),
        ),
        operation,
      ),
      compaction_metadata: state.compaction_metadata,
    }));
  }

  prepareHistoryItemForModelInput(item: AgentInputItem): AgentInputItem {
    const preparedFunctionItem = prepareFunctionItemForModelInput(item);
    if (preparedFunctionItem !== item) return preparedFunctionItem;
    if (
      !("role" in item) ||
      item.role !== "user" ||
      !Array.isArray(item.content)
    )
      return item;
    const content = (item.content as Array<{ type: string }>).flatMap(
      (part) => {
        if (part.type === "input_image") return [];
        if (part.type === "input_file")
          return [unsupportedFileContentToText(part)];
        return [part];
      },
    );
    return { ...item, content } as AgentInputItem;
  }

  async addItems(items: AgentInputItem[]): Promise<void> {
    await this.mutate((state) => ({
      items: [...state.items, ...items],
      coaching_memory: state.coaching_memory,
      compaction_metadata: state.compaction_metadata,
    }));
  }

  async popItem(): Promise<AgentInputItem | undefined> {
    let popped: AgentInputItem | undefined;
    await this.mutate((state) => {
      popped = state.items.at(-1);
      return {
        items: state.items.slice(0, -1),
        coaching_memory: state.coaching_memory,
        compaction_metadata: state.compaction_metadata,
      };
    });
    return popped;
  }

  async clearSession(): Promise<void> {
    await this.replaceAll([], {});
  }

  async replaceAll(
    items: AgentInputItem[],
    metadata: Record<string, unknown>,
  ): Promise<void> {
    await this.mutate((state) => ({
      items,
      coaching_memory: state.coaching_memory,
      compaction_metadata: { ...state.compaction_metadata, ...metadata },
    }));
  }

  async applyHistoryMutations({
    mutations,
  }: SessionHistoryRewriteArgs): Promise<void> {
    await this.mutate((state) => ({
      items: state.items.map((item) => {
        if (!("type" in item) || item.type !== "function_call") return item;
        const replacement = mutations.find(
          (entry) => entry.callId === item.callId,
        );
        return replacement?.replacement ?? item;
      }),
      coaching_memory: state.coaching_memory,
      compaction_metadata: state.compaction_metadata,
    }));
  }
}
