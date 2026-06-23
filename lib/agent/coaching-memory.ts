import { z } from "zod";

export const coachingMemoryRecordSchema = z.object({
  id: z.string().min(1),
  category: z.enum([
    "commitment",
    "constraint",
    "follow_up",
    "insight",
    "outcome",
    "preference",
  ]),
  statement: z.string().min(1),
  confidence: z.number().min(0).max(1),
  sourceMessageIds: z.array(z.string().min(1)).min(1),
  effectiveFrom: z.string().optional(),
  effectiveUntil: z.string().optional(),
  lifecycle: z.enum(["active", "superseded", "resolved", "dismissed"]),
  supersededBy: z.string().optional(),
  plannedDate: z.iso.date().optional(),
  followUpAt: z
    .iso.datetime({ offset: true })
    .transform((value) => new Date(value).toISOString())
    .optional(),
  outcome: z.string().optional(),
});

export type CoachingMemoryRecord = z.infer<typeof coachingMemoryRecordSchema>;

const newRecordSchema = coachingMemoryRecordSchema
  .omit({ lifecycle: true })
  .partial({ followUpAt: true });

export const coachingMemoryOperationSchema = z.discriminatedUnion("action", [
  z.object({ action: z.literal("upsert"), record: newRecordSchema }),
  z.object({
    action: z.literal("supersede"),
    id: z.string(),
    replacement: newRecordSchema,
  }),
  z.object({
    action: z.literal("resolve"),
    id: z.string(),
    outcome: z.string().optional(),
  }),
  z.object({ action: z.literal("dismiss"), id: z.string() }),
]);
export const coachingMemoryToolSchema = z.object({
  operation: coachingMemoryOperationSchema,
});
export type CoachingMemoryOperation = z.infer<
  typeof coachingMemoryOperationSchema
>;

export function dueFollowUpAt(
  record: Pick<CoachingMemoryRecord, "plannedDate" | "followUpAt">,
): string | undefined {
  if (record.followUpAt) return record.followUpAt;
  if (!record.plannedDate) return undefined;
  const planned = new Date(`${record.plannedDate}T12:00:00.000Z`);
  planned.setUTCDate(planned.getUTCDate() + 1);
  return planned.toISOString();
}

function activeRecord(
  record: z.infer<typeof newRecordSchema>,
): CoachingMemoryRecord {
  const parsed = newRecordSchema.parse(record);
  return coachingMemoryRecordSchema.parse({
    ...parsed,
    lifecycle: "active",
    followUpAt: dueFollowUpAt(parsed),
  });
}

export function applyMemoryOperation(
  records: CoachingMemoryRecord[],
  rawOperation: CoachingMemoryOperation,
): CoachingMemoryRecord[] {
  const operation = coachingMemoryOperationSchema.parse(rawOperation);
  if (operation.action === "upsert") {
    const record = activeRecord(operation.record);
    return [...records.filter((item) => item.id !== record.id), record];
  }
  if (operation.action === "supersede") {
    const replacement = activeRecord(operation.replacement);
    return [
      ...records.map((item) =>
        item.id === operation.id
          ? {
              ...item,
              lifecycle: "superseded" as const,
              supersededBy: replacement.id,
            }
          : item,
      ),
      replacement,
    ];
  }
  return records.map((item) =>
    item.id === operation.id
      ? {
          ...item,
          lifecycle:
            operation.action === "resolve"
              ? ("resolved" as const)
              : ("dismissed" as const),
          ...(operation.action === "resolve" && operation.outcome
            ? { outcome: operation.outcome }
            : {}),
        }
      : item,
  );
}

export function oldestDueFollowUp(
  records: CoachingMemoryRecord[],
  now = new Date(),
): CoachingMemoryRecord | undefined {
  return records
    .filter(
      (record) =>
        record.lifecycle === "active" &&
        record.followUpAt &&
        new Date(record.followUpAt) <= now,
    )
    .sort((left, right) =>
      String(left.followUpAt).localeCompare(String(right.followUpAt)),
    )[0];
}
