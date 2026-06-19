import type { UIMessage } from "ai";
import { afterEach, describe, expect, it, vi } from "vitest";

import { buildContextSlices } from "../../lib/agent/context-slices";
import { loadAgentModelPolicy } from "../../lib/agent/model-policy";
import type {
  InternalSpecialistRole,
  SpecialistReport,
} from "../../lib/agent/orchestration-types";
import { runSpecialists } from "../../lib/agent/specialists";

import { athleteContextFixture } from "./agent-fixtures";

const specialistMocks = vi.hoisted(() => ({
  generateText: vi.fn(),
}));

vi.mock("ai", () => ({
  convertToModelMessages: vi.fn((messages: UIMessage[]) =>
    Promise.resolve(messages),
  ),
  generateText: specialistMocks.generateText,
  Output: {
    object: vi.fn(({ schema }) => ({ schema })),
  },
}));

const messages: UIMessage[] = [
  {
    id: "63ff9606-9158-43d7-a82b-d31ef9788b7d",
    parts: [{ text: "I am tired after today's workout.", type: "text" }],
    role: "user",
  },
];

function report(role: InternalSpecialistRole): SpecialistReport {
  return {
    confidence: "medium",
    proposedUpdates: [],
    risks: [],
    role,
    summary: `${role} report`,
  };
}

function deferred<T>(): {
  promise: Promise<T>;
  reject: (error: unknown) => void;
  resolve: (value: T) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("runSpecialists", () => {
  it("starts independent specialists concurrently and returns deterministic order", async () => {
    const recovery = deferred<{ output: SpecialistReport }>();
    const workout = deferred<{ output: SpecialistReport }>();
    specialistMocks.generateText
      .mockImplementationOnce(() => recovery.promise)
      .mockImplementationOnce(() => workout.promise);

    const pendingReports = runSpecialists({
      messages,
      model: "model" as never,
      modelPolicy: loadAgentModelPolicy({}),
      roles: ["workout", "recovery"],
      slices: buildContextSlices(athleteContextFixture),
    });

    await vi.waitFor(() => {
      expect(specialistMocks.generateText).toHaveBeenCalledTimes(2);
    });

    // Verify specialist policy settings are forwarded
    const firstCall = specialistMocks.generateText.mock.calls[0]?.[0];
    const secondCall = specialistMocks.generateText.mock.calls[1]?.[0];
    expect(firstCall).toMatchObject({
      maxRetries: 2,
      providerOptions: {
        openai: {
          reasoningEffort: "low",
          textVerbosity: "low",
        },
      },
      timeout: {
        totalMs: 30_000,
      },
    });
    expect(secondCall).toMatchObject({
      maxRetries: 2,
      providerOptions: {
        openai: {
          reasoningEffort: "low",
          textVerbosity: "low",
        },
      },
      timeout: {
        totalMs: 30_000,
      },
    });

    workout.resolve({ output: report("workout") });
    recovery.resolve({ output: report("recovery") });

    await expect(pendingReports).resolves.toEqual([
      report("recovery"),
      report("workout"),
    ]);
  });

  it("returns successful reports when one specialist fails", async () => {
    specialistMocks.generateText
      .mockRejectedValueOnce(new Error("upstream unavailable"))
      .mockResolvedValueOnce({ output: report("workout") });
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    await expect(
      runSpecialists({
        messages,
        model: "model" as never,
        modelPolicy: loadAgentModelPolicy({}),
        roles: ["recovery", "workout"],
        slices: buildContextSlices(athleteContextFixture),
      }),
    ).resolves.toEqual([report("workout")]);
    expect(errorSpy).toHaveBeenCalledWith(
      "[chat] specialist failed:",
      "recovery",
      "Error",
    );
  });
});
