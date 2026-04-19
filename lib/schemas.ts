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
  content_length: z.number().int().min(1).max(25 * 1024 * 1024),
  content_type: z.string().trim().min(1),
  filename: z.string().trim().min(1),
  purpose: z.string().trim().min(1).default("check-in-image")
});

export const chatAttachmentSchema = z.object({
  content_type: z.string().trim().min(1),
  filename: z.string().trim().min(1),
  object_key: z.string().trim().min(1),
  public_url: z.string().trim().url().nullable()
});

export const chatMessageSchema = z.object({
  attachments: z.array(chatAttachmentSchema).default([]),
  content: z.string().max(8000).default("")
});

export type AthleteProfileInput = z.infer<typeof athleteProfileSchema>;
export type ChatAttachmentInput = z.infer<typeof chatAttachmentSchema>;
export type ChatMessageInput = z.infer<typeof chatMessageSchema>;
export type UploadRequestInput = z.infer<typeof uploadRequestSchema>;
