export type BrowserTokenResponse = {
  access_token: string;
  expires_at: string;
  scopes: string[];
  token_type: "Bearer";
  user_id: string;
};

export type AthleteProfile = {
  age?: number;
  constraints: string[];
  cycling_ftp_watts?: number;
  goals: string[];
  injuries_rehab: string[];
  notes?: string;
  user_id: string;
  weight_kg?: number;
};

export type CheckInInput = {
  effective_date?: string;
  image_count: number;
  raw_text: string;
  user_id: string;
};

export type PlanDay = {
  day_index: number;
  focus: string;
  notes: string;
};

export type AdaptedPlan = {
  generated_at: string;
  hours: number;
  summary: string;
  trend: string;
  user_id: string;
  days: PlanDay[];
};

export type GeneratedPlanResponse = {
  plan: AdaptedPlan;
  prompt_preview: string;
};

export type CheckInRecord = {
  created_at: string;
  effective_date?: string;
  id: string;
  image_count: number;
  raw_text: string;
  user_id: string;
};

export type CheckInResponse = {
  accepted: boolean;
  check_in: CheckInRecord;
};

export type PresignUploadRequest = {
  content_length: number;
  content_type: string;
  filename: string;
  purpose: string;
};

export type PresignUploadResponse = {
  headers: Record<string, string>;
  method: string;
  object_key: string;
  public_url: string | null;
  upload_url: string;
};
