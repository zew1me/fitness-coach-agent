import type { AgentInputItem } from "@openai/agents";
import { z } from "zod";

import type {
  ChatMessage,
  IntervalsSyncResponse,
  StravaDisconnectResponse,
  StravaSyncResponse,
} from "./types";

export const athleteProfileSchema = z.object({
  coaching_state: z.string().min(1).default("onboarding"),
  dietary_restrictions: z.array(z.string().trim().min(1)).optional(),
  display_name: z.string().trim().min(1).nullable().optional(),
  nutrition_notes: z.string().trim().min(1).nullable().optional(),
  primary_sports: z.array(z.string().trim().min(1)).default([]),
  user_id: z.string().uuid().or(z.string().min(1)),
  weekly_available_hours: z.number().positive().nullable().optional(),
});

const chatMessagePartSchema = z.looseObject({
  type: z.string().trim().min(1),
});

export const chatRequestMessageSchema = z.looseObject({
  id: z.string().min(1),
  role: z.enum(["system", "user", "assistant"]),
  parts: z.array(chatMessagePartSchema),
});

export const chatRequestBodySchema = z.union([
  z.strictObject({
    id: z.string().optional(),
    message: chatRequestMessageSchema,
    messages: z.never().optional(),
  }),
  z.strictObject({
    id: z.string().optional(),
    message: z.never().optional(),
    messages: z.array(chatRequestMessageSchema),
  }),
]);

export type ChatRequestBody = z.infer<typeof chatRequestBodySchema>;

const chatMessageMetadataSchema = z
  .object({
    message_kind: z.string().optional(),
    pending_profile_field: z.string().nullable().optional(),
    plan: z.unknown().optional(),
  })
  .catchall(z.unknown());

export const chatMessageSchema = z.preprocess(
  (raw) => {
    if (raw !== null && typeof raw === "object" && !Array.isArray(raw)) {
      const msg = raw as Record<string, unknown>;
      if (!Array.isArray(msg["parts"])) {
        const content = msg["content"];
        const parts =
          typeof content === "string" && content.length > 0
            ? [{ type: "text", text: content }]
            : [];
        return { ...msg, parts };
      }
    }
    return raw;
  },
  z
    .object({
      attachments: z.array(z.record(z.string(), z.unknown())),
      content: z.string().default(""),
      created_at: z.string(),
      id: z.string().min(1),
      metadata: chatMessageMetadataSchema,
      parts: z.array(chatMessagePartSchema),
      role: z.enum(["user", "assistant"]),
      thread_id: z.string().min(1),
      user_id: z.string().min(1),
    })
    .transform((message): ChatMessage => message as ChatMessage),
);

export const chatMessagePageSchema = z.object({
  messages: z.array(chatMessageSchema),
  next_cursor: z.string().nullable(),
});

export type ParsedChatMessagePage = z.infer<typeof chatMessagePageSchema>;

export const chatThreadResponseSchema = z.object({
  attachments_enabled: z.boolean(),
  next_cursor: z.string().nullable().default(null),
  profile_complete: z.boolean(),
  thread: z.object({
    created_at: z.string(),
    id: z.string().min(1),
    messages: z.array(chatMessageSchema),
    state: z.record(z.string(), z.unknown()),
    updated_at: z.string(),
    user_id: z.string().min(1),
  }),
});

export type ParsedChatThreadResponse = z.infer<typeof chatThreadResponseSchema>;

export const chatTurnLeaseStatusSchema = z.object({
  expires_at: z.string().datetime().nullable(),
  in_flight: z.boolean(),
});

export type ParsedChatTurnLeaseStatus = z.infer<
  typeof chatTurnLeaseStatusSchema
>;

export const intervalsConnectionStatusSchema = z.object({
  connected: z.boolean(),
  connected_at: z.string().nullable().optional(),
  intervals_athlete_id: z.string().nullable().optional(),
  intervals_athlete_name: z.string().nullable().optional(),
  scopes: z.array(z.string()).default([]),
});

export const intervalsAuthorizeResponseSchema = z.object({
  redirect_url: z.string().url(),
});

export const intervalsSyncRequestSchema = z.object({
  days: z.number().int().min(1).max(90),
});

export const intervalsSyncResponseSchema = z
  .object({
    activities: z.array(z.record(z.string(), z.unknown())),
    skipped_duplicates: z.number().int().nonnegative(),
    skipped_invalid: z.number().int().nonnegative(),
    synced: z.number().int().nonnegative(),
  })
  .transform((response): IntervalsSyncResponse => response);

export const stravaConnectionStatusSchema = z.object({
  connected: z.boolean(),
  disconnect_pending: z.boolean().optional(),
  connected_at: z.string().nullable().optional(),
  last_sync_at: z.string().nullable().optional(),
  strava_athlete_id: z.number().nullable().optional(),
  strava_athlete_name: z.string().nullable().optional(),
  scopes: z.array(z.string()).default([]),
  authorization_version: z.string().nullable().optional(),
});

export const stravaAuthorizeResponseSchema = z.object({
  redirect_url: z.string().url(),
});

// Keep the max aligned with the backend StravaSyncRequest bound.
export const stravaSyncRequestSchema = z.object({
  days: z.number().int().min(1).max(90),
});

export const stravaSyncResponseSchema = z
  .object({
    activities: z.array(z.record(z.string(), z.unknown())),
    skipped_duplicates: z.number().int().nonnegative(),
    skipped_invalid: z.number().int().nonnegative(),
    synced: z.number().int().nonnegative(),
  })
  .transform((response): StravaSyncResponse => response);

export const stravaDisconnectResponseSchema = z
  .object({
    connected: z.boolean(),
    disconnect_pending: z.boolean().optional(),
    connected_at: z.string().nullable().optional(),
    last_sync_at: z.string().nullable().optional(),
    strava_athlete_id: z.number().nullable().optional(),
    strava_athlete_name: z.string().nullable().optional(),
    scopes: z.array(z.string()).default([]),
    authorization_version: z.string().nullable().optional(),
    deleted_activities: z.number().int().nonnegative().default(0),
  })
  .transform((response): StravaDisconnectResponse => response);

export const modelStateSchema = z.object({
  thread_id: z.string().min(1),
  version: z.number().int().nonnegative(),
  items: z.array(z.custom<AgentInputItem>()),
  coaching_memory: z.array(z.record(z.string(), z.unknown())),
  compaction_metadata: z.record(z.string(), z.unknown()),
});

export type ModelState = z.infer<typeof modelStateSchema>;

export const uploadRequestSchema = z.object({
  content_length: z
    .number()
    .int()
    .min(1)
    .max(25 * 1024 * 1024),
  content_type: z.string().trim().min(1),
  filename: z.string().trim().min(1),
  purpose: z.string().trim().min(1).default("check-in-image"),
});

const isoDateSchema = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);

export const calendarPlannedWorkoutSchema = z.looseObject({
  id: z.string().min(1),
  plan_id: z.string().min(1),
  workout_date: isoDateSchema,
  sport: z.string().min(1),
  title: z.string().min(1),
  description: z.string().nullable().optional(),
  workout_type: z.string().min(1),
  phase_name: z.string().nullable().optional(),
  target_duration_minutes: z.number().nullable().optional(),
  target_distance_meters: z.number().nullable().optional(),
  target_tss: z.number().nullable().optional(),
  status: z.string().min(1).default("scheduled"),
  actual_activity_id: z.string().nullable().optional(),
});

export const calendarActivitySchema = z.looseObject({
  id: z.string().min(1),
  sport: z.string().min(1),
  activity_date: isoDateSchema,
  started_at: z.string().nullable().optional(),
  duration_seconds: z.number().nullable().optional(),
  distance_meters: z.number().nullable().optional(),
  elevation_gain_meters: z.number().nullable().optional(),
  avg_hr_bpm: z.number().nullable().optional(),
  tss: z.number().nullable().optional(),
  rpe: z.number().nullable().optional(),
  athlete_notes: z.string().nullable().optional(),
  planned_workout_id: z.string().nullable().optional(),
});

export const resolvePlanWorkoutResponseSchema = z.object({
  workout: calendarPlannedWorkoutSchema,
});

export const calendarResponseSchema = z.object({
  start: isoDateSchema,
  end: isoDateSchema,
  planned_workouts: z.array(calendarPlannedWorkoutSchema),
  activities: z.array(calendarActivitySchema),
});

export type CalendarPlannedWorkout = z.infer<
  typeof calendarPlannedWorkoutSchema
>;
export type CalendarActivity = z.infer<typeof calendarActivitySchema>;
export type CalendarResponse = z.infer<typeof calendarResponseSchema>;
