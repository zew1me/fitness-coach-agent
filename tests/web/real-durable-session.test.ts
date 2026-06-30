import {
  Agent,
  run,
  type AgentInputItem,
  type SessionHistoryRewriteArgs,
  type SessionHistoryRewriteAwareSession,
} from "@openai/agents";
import { describe, expect, it } from "vitest";

import { planSpecialistDelegation } from "../../lib/agent/delegation-planner";
import { DurableCompactionSession } from "../../lib/agent/supabase-agent-session";

import { athleteContextFixture } from "./agent-fixtures";

class ReloadableTestSession implements SessionHistoryRewriteAwareSession {
  constructor(private items: AgentInputItem[]) {}

  getSessionId(): Promise<string> {
    return Promise.resolve("real-api-compaction-test");
  }

  getItems(limit?: number): Promise<AgentInputItem[]> {
    return Promise.resolve(
      limit === undefined ? [...this.items] : this.items.slice(-limit),
    );
  }

  addItems(items: AgentInputItem[]): Promise<void> {
    this.items.push(...items);
    return Promise.resolve();
  }

  popItem(): Promise<AgentInputItem | undefined> {
    return Promise.resolve(this.items.pop());
  }

  clearSession(): Promise<void> {
    this.items = [];
    return Promise.resolve();
  }

  replaceAll(items: AgentInputItem[]): Promise<void> {
    this.items = structuredClone(items);
    return Promise.resolve();
  }

  applyHistoryMutations(_args: SessionHistoryRewriteArgs): Promise<void> {
    return Promise.resolve();
  }
}

const liveDescribe =
  process.env["RUN_OPENAI_INTEGRATION"] === "1" ? describe : describe.skip;

liveDescribe("real OpenAI durable-session continuity", () => {
  it("compacts, reloads, delegates from prior detail, and preserves final continuity", async () => {
    const syntheticHistory: AgentInputItem[] = Array.from(
      { length: 30 },
      (_, index) => ({
        role: index % 2 === 0 ? "user" : "assistant",
        status: "completed",
        content: [
          index === 28
            ? {
                type: "input_text",
                text: "My right knee hurts after downhill running.",
              }
            : index % 2 === 0
              ? { type: "input_text", text: `Athlete history item ${index}` }
              : { type: "output_text", text: `Coach history item ${index}` },
        ],
      }),
    ) as AgentInputItem[];
    const store = new ReloadableTestSession(syntheticHistory);
    const compacting = new DurableCompactionSession({
      underlyingSession: store,
    });

    await compacting.runCompaction({ force: true, compactionMode: "input" });
    const reloaded = await store.getItems();
    expect(
      reloaded.some((item) => "type" in item && item.type === "compaction"),
    ).toBe(true);

    const latest: AgentInputItem[] = [
      {
        role: "user",
        content: [
          {
            type: "input_text",
            text: "Given what I told you, should I do tomorrow's intervals?",
          },
        ],
      },
    ];
    const plan = await planSpecialistDelegation({
      durableContext: reloaded,
      latestUserTurn: latest,
      coachingMemory: [],
      athleteContext: athleteContextFixture,
      model: "gpt-5.4-mini",
    });
    expect(plan.delegations.length).toBeLessThanOrEqual(2);
    expect(plan.delegations.map((item) => item.role)).toContain("recovery");

    const lead = new Agent({
      name: "Continuity verifier",
      model: "gpt-5.4-mini",
      instructions: `Answer the latest turn using the durable context and this delegation plan: ${JSON.stringify(plan)}. Explicitly name the relevant prior issue.`,
    });
    const result = await run(lead, [...reloaded, ...latest], { maxTurns: 1 });
    expect(String(result.finalOutput)).toMatch(/knee/i);
  }, 180_000);
});
