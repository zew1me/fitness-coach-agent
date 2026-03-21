import { z } from "zod";

export const athleteProfileSchema = z.object({
  id: z.string().uuid().or(z.string().min(1)),
  name: z.string().trim().min(1).optional(),
  age: z.number().int().min(13).max(120).optional(),
  cyclingFtpWatts: z.number().int().positive().optional(),
  weightKg: z.number().positive().optional(),
  goals: z.array(z.string().trim().min(1)).optional(),
  constraints: z.array(z.string().trim().min(1)).optional(),
  notes: z.string().trim().min(1).optional()
});

export const planRequestSchema = z.object({
  userId: z.string().uuid().or(z.string().min(1)),
  rawText: z.string().trim().min(1),
  imageCount: z.number().int().min(0).default(0),
  effectiveDate: z.string().optional()
});

export type AthleteProfileInput = z.infer<typeof athleteProfileSchema>;
export type PlanRequestInput = z.infer<typeof planRequestSchema>;
