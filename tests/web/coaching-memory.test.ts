import { describe, expect, it } from "vitest";

import {
  applyMemoryOperation,
  coachingMemoryRecordSchema,
  dueFollowUpAt,
  oldestDueFollowUp,
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

  it("rejects superseding a missing record", () => {
    expect(() =>
      applyMemoryOperation([], {
        action: "supersede",
        id: "missing",
        replacement: {
          id: "new",
          category: "follow_up",
          statement: "Do intervals",
          confidence: 1,
          sourceMessageIds: ["m2"],
        },
      }),
    ).toThrow(/missing/i);
  });

  it("rejects supersede replacements that duplicate an existing record id", () => {
    const records = [
      coachingMemoryRecordSchema.parse({
        id: "old",
        category: "follow_up",
        statement: "Do intervals",
        confidence: 1,
        sourceMessageIds: ["m1"],
        lifecycle: "active",
      }),
      coachingMemoryRecordSchema.parse({
        id: "duplicate",
        category: "insight",
        statement: "Prefers trails",
        confidence: 0.8,
        sourceMessageIds: ["m1"],
        lifecycle: "active",
      }),
    ];

    expect(() =>
      applyMemoryOperation(records, {
        action: "supersede",
        id: "old",
        replacement: {
          id: "duplicate",
          category: "follow_up",
          statement: "Do intervals tomorrow",
          confidence: 1,
          sourceMessageIds: ["m2"],
        },
      }),
    ).toThrow(/duplicate/i);
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

  it("rejects resolve and dismiss operations for missing record ids", () => {
    expect(() =>
      applyMemoryOperation([], {
        action: "resolve",
        id: "missing",
        outcome: "Done",
      }),
    ).toThrow(/missing/i);
    expect(() =>
      applyMemoryOperation([], { action: "dismiss", id: "missing" }),
    ).toThrow(/missing/i);
  });

  it("dismisses a record without an outcome", () => {
    const next = applyMemoryOperation(
      [
        {
          id: "note",
          category: "insight",
          statement: "Athlete skipped warmup",
          confidence: 0.8,
          sourceMessageIds: ["m1"],
          lifecycle: "active",
        },
      ],
      { action: "dismiss", id: "note" },
    );
    expect(next).toHaveLength(1);
    expect(next[0]).toMatchObject({ lifecycle: "dismissed" });
  });

  it("upserts by id, replacing an existing record with the same id", () => {
    const base = {
      id: "commitment-1",
      category: "commitment" as const,
      statement: "Run a 5k",
      confidence: 0.7,
      sourceMessageIds: ["m1"],
      lifecycle: "active" as const,
    };
    const next = applyMemoryOperation([base], {
      action: "upsert",
      record: {
        id: "commitment-1",
        category: "commitment",
        statement: "Run a sub-25 5k",
        confidence: 0.9,
        sourceMessageIds: ["m1"],
      },
    });
    expect(next).toHaveLength(1);
    expect(next[0]!.statement).toBe("Run a sub-25 5k");
  });

  it("upserts by id, appending when no existing record matches", () => {
    const next = applyMemoryOperation([], {
      action: "upsert",
      record: {
        id: "new-commitment",
        category: "commitment",
        statement: "Complete a triathlon",
        confidence: 1,
        sourceMessageIds: ["m2"],
      },
    });
    expect(next).toHaveLength(1);
    expect(next[0]!.id).toBe("new-commitment");
  });
});

describe("oldestDueFollowUp", () => {
  const now = new Date("2026-06-24T12:00:00.000Z");

  it("returns undefined when no records are active with a past followUpAt", () => {
    expect(oldestDueFollowUp([], now)).toBeUndefined();
  });

  it("ignores records whose followUpAt is in the future", () => {
    const result = oldestDueFollowUp(
      [
        {
          id: "f1",
          category: "follow_up",
          statement: "Check in next week",
          confidence: 1,
          sourceMessageIds: ["m1"],
          lifecycle: "active",
          followUpAt: "2026-06-30T12:00:00.000Z",
        },
      ],
      now,
    );
    expect(result).toBeUndefined();
  });

  it("ignores records that are not active", () => {
    const result = oldestDueFollowUp(
      [
        {
          id: "f1",
          category: "follow_up",
          statement: "Already resolved",
          confidence: 1,
          sourceMessageIds: ["m1"],
          lifecycle: "resolved",
          followUpAt: "2026-06-01T00:00:00.000Z",
        },
      ],
      now,
    );
    expect(result).toBeUndefined();
  });

  it("returns the oldest due follow-up when multiple are past due", () => {
    const older = {
      id: "f1",
      category: "follow_up" as const,
      statement: "Older",
      confidence: 1,
      sourceMessageIds: ["m1"],
      lifecycle: "active" as const,
      followUpAt: "2026-06-10T00:00:00.000Z",
    };
    const newer = {
      id: "f2",
      category: "follow_up" as const,
      statement: "Newer",
      confidence: 1,
      sourceMessageIds: ["m2"],
      lifecycle: "active" as const,
      followUpAt: "2026-06-20T00:00:00.000Z",
    };
    expect(oldestDueFollowUp([newer, older], now)!.id).toBe("f1");
  });
});
