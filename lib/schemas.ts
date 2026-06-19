import { z } from "zod";

export const athleteProfileSchema = z.object({
  coaching_state: z.string().min(1).default("onboarding"),
  dietary_restrictions: z.array(z.string().trim().min(1)).optional(),
  display_name: z.string().trim().min(1).nullable().optional(),
  nutrition_notes: z.string().trim().min(1).nullable().optional(),
  primary_sports: z.array(z.string().trim().min(1)).default([]),
  user_id: z.string().uuid().or(z.string().min(1)),
  weekly_available_hours: z.number().positive().nullable().optional(),
});

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

const messagePartSchema = z.union([
  z.object({
    type: z.literal("text"),
    text: z.string(),
  }),
  z.object({
    type: z.literal("image"),
    url: z.string(),
    mimeType: z.string().optional(),
  }),
  z.object({
    type: z.literal("file"),
    url: z.string(),
    mediaType: z.string().optional(),
    filename: z.string().optional(),
  }),
  z.object({
    type: z.literal("tool-call"),
    toolCallId: z.string(),
    toolName: z.string(),
    args: z.unknown(),
  }),
  z.object({
    type: z.literal("tool-result"),
    toolCallId: z.string(),
    toolName: z.string(),
    result: z.unknown(),
  }),
  // Catch-all for AI SDK v5 part types not enumerated above (reasoning, step-start,
  // dynamic tool parts like tool-tavilySearch, etc.) — validates envelope only.
  z.object({ type: z.string() }).passthrough(),
]);

const uiMessageSchema = z.object({
  id: z.string(),
  role: z.enum(["user", "assistant"]),
  parts: z.array(messagePartSchema),
});

export const chatRequestBodySchema = z.object({
  messages: z.array(uiMessageSchema).optional(),
});
