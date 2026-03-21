export type AthleteProfile = {
  age?: number;
  constraints?: string[];
  cyclingFtpWatts?: number;
  goals?: string[];
  id: string;
  name?: string;
  notes?: string;
  weightKg?: number;
};

export type CheckInInput = {
  effectiveDate?: string;
  imageCount: number;
  rawText: string;
};

export type PlanDay = {
  dayIndex: number;
  focus: string;
  notes: string;
};

export type AdaptedPlan = {
  hours: number;
  summary: string;
  trend: string;
  userId: string;
  days: PlanDay[];
};
