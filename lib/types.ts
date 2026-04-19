export type BrowserTokenResponse = {
  access_token: string;
  expires_at: string;
  scopes: string[];
  token_type: "Bearer";
  user_id: string;
};

export type AthleteProfile = {
  coaching_state: string;
  dietary_restrictions?: string[];
  display_name?: string | null;
  nutrition_notes?: string | null;
  primary_sports: string[];
  user_id: string;
  weekly_available_hours?: number | null;
};

export type AdaptedPlan = {
  generated_at: string;
  hours: number;
  summary: string;
  trend: string;
  user_id: string;
  days: { day_index: number; focus: string; notes: string }[];
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

export type ChatAttachment = {
  content_type: string;
  created_at?: string;
  filename: string;
  id?: string;
  message_id?: string;
  object_key: string;
  public_url: string | null;
  user_id?: string;
};

export type ChatMessage = {
  attachments: ChatAttachment[];
  content: string;
  created_at: string;
  id: string;
  metadata: {
    message_kind?: string;
    pending_profile_field?: string | null;
    plan?: AdaptedPlan;
  } & Record<string, unknown>;
  role: "user" | "assistant";
  thread_id: string;
  user_id: string;
};

export type ChatThread = {
  created_at: string;
  id: string;
  messages: ChatMessage[];
  state: Record<string, unknown>;
  updated_at: string;
  user_id: string;
};

export type ChatThreadResponse = {
  attachments_enabled: boolean;
  profile_complete: boolean;
  thread: ChatThread;
};
