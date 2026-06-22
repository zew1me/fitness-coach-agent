import { describe, expect, it } from "vitest";

import {
  applyMemoryOperation,
  coachingMemoryRecordSchema,
  dueFollowUpAt,
} from "../../lib/agent/coaching-memory";

describe("coaching memory lifecycle", () => {
  it("rejects malformed plan and follow-up dates", () => {
    const base = {
      id: "invalid-date",
      category: "follow_up" as const,
      statement: "Do intervals",
      confidence: 1,
      sourceMessageIds: ["m1"],
      lifecycle: "active" as const,
    };

    expect(() =>
      coachingMemoryRecordSchema.parse({ ...base, plannedDate: "tomorrow" }),
    ).toThrow();
    expect(() =>
      coachingMemoryRecordSchema.parse({ ...base, followUpAt: "next week" }),
    ).toThrow();
  });

  it("supersedes a rescheduled plan and creates a new pending record", () => {
    const existing = [
      coachingMemoryRecordSchema.parse({
        id: "old",
        category: "follow_up",
        statement: "Do intervals",
        confidence: 1,
        sourceMessageIds: ["m1"],
        lifecycle: "active",
        plannedDate: "2026-06-20",
      }),
    ];

    const next = applyMemoryOperation([...existing], {
      action: "supersede",
      id: "old",
      replacement: {
        id: "new",
        category: "follow_up",
        statement: "Do intervals",
        confidence: 1,
        sourceMessageIds: ["m2"],
        plannedDate: "2026-06-22",
      },
    });

    expect(next.find((item) => item.id === "old")?.lifecycle).toBe(
      "superseded",
    );
    expect(next.find((item) => item.id === "new")).toMatchObject({
      lifecycle: "active",
      plannedDate: "2026-06-22",
    });
  });

  it("makes a date-only plan due at noon UTC the following day", () => {
    expect(dueFollowUpAt({ plannedDate: "2026-06-20" })).toBe(
      "2026-06-21T12:00:00.000Z",
    );
  });

  it("resolves a volunteered outcome without creating another record", () => {
    const next = applyMemoryOperation(
      [
        {
          id: "run",
          category: "follow_up",
          statement: "Complete long run",
          confidence: 1,
          sourceMessageIds: ["m1"],
          lifecycle: "active",
          plannedDate: "2026-06-20",
        },
      ],
      { action: "resolve", id: "run", outcome: "Completed comfortably" },
    );
    expect(next).toHaveLength(1);
    expect(next[0]).toMatchObject({
      lifecycle: "resolved",
      outcome: "Completed comfortably",
    });
  });
});
