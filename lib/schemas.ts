import { z } from "zod";

export const athleteProfileSchema = z.object({
  user_id: z.string().uuid().or(z.string().min(1)),
  age: z.number().int().min(13).max(120).optional(),
  cycling_ftp_watts: z.number().int().positive().optional(),
  weight_kg: z.number().positive().optional(),
  goals: z.array(z.string().trim().min(1)).default([]),
  constraints: z.array(z.string().trim().min(1)).default([]),
  injuries_rehab: z.array(z.string().trim().min(1)).default([]),
  notes: z.string().trim().min(1).optional()
});

export const planRequestSchema = z.object({
  user_id: z.string().uuid().or(z.string().min(1)),
  raw_text: z.string().trim().min(1),
  image_count: z.number().int().min(0).default(0),
  effective_date: z.string().optional()
});

export const uploadRequestSchema = z.object({
  content_length: z.number().int().min(1).max(25 * 1024 * 1024),
  content_type: z.string().trim().min(1),
  filename: z.string().trim().min(1),
  purpose: z.string().trim().min(1).default("check-in-image")
});

export type AthleteProfileInput = z.infer<typeof athleteProfileSchema>;
export type PlanRequestInput = z.infer<typeof planRequestSchema>;
export type UploadRequestInput = z.infer<typeof uploadRequestSchema>;
