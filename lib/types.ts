export type ThresholdSource = "user" | "file" | "estimated";

export type ThresholdValue = {
  value: number;
  unit: string;
  source: ThresholdSource;
  measured_at: string | null;
  notes: string | null;
};

export type BestTime = {
  distance_label: string;
  time_seconds: number;
  measured_at: string | null;
};

export type FitnessMetrics = {
  cycling_ftp?: ThresholdValue;
  max_hr?: ThresholdValue;
  run_threshold_pace?: ThresholdValue;
  swim_css?: ThresholdValue;
  weight?: ThresholdValue;
  best_times: BestTime[];
};

export type BrowserTokenResponse = {
  access_token: string;
  expires_at: string;
  scopes: string[];
  token_type: "Bearer";
  user_id: string;
};

export type IntervalsConnectionStatus = {
  connected: boolean;
  connected_at?: string | null | undefined;
  intervals_athlete_id?: string | null | undefined;
  intervals_athlete_name?: string | null | undefined;
  scopes: string[];
};

export type IntervalsSyncResponse = {
  activities: Record<string, unknown>[];
  skipped_duplicates: number;
  skipped_invalid: number;
  synced: number;
};

export type StravaConnectionStatus = {
  connected: boolean;
  disconnect_pending?: boolean | undefined;
  connected_at?: string | null | undefined;
  last_sync_at?: string | null | undefined;
  strava_athlete_id?: number | null | undefined;
  strava_athlete_name?: string | null | undefined;
  scopes: string[];
  authorization_version?: string | null | undefined;
};

export type StravaSyncResponse = {
  activities: Record<string, unknown>[];
  skipped_duplicates: number;
  skipped_invalid: number;
  synced: number;
};

export type StravaDisconnectResponse = StravaConnectionStatus & {
  deleted_activities: number;
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

// MessagePart and MessageAttachment mirror the AI SDK UIMessage shape. They
// are deliberately permissive so new AI-SDK part types (tool-*, reasoning,
// data-*) round-trip through the backend without churn.
export type MessagePart = Record<string, unknown> & { type: string };
export type MessageAttachment = Record<string, unknown>;

export type ChatMessage = {
  attachments: MessageAttachment[];
  // `content` is a denormalized text mirror kept during the parts-JSON
  // migration window; renderers should drive off `parts` instead.
  content?: string;
  created_at: string;
  id: string;
  metadata: {
    message_kind?: string;
    pending_profile_field?: string | null;
    plan?: AdaptedPlan;
  } & Record<string, unknown>;
  // The chat-parts backfill migration guarantees `parts` is non-empty on every persisted
  // row. Optional here only because legacy in-flight payloads (tests, transient
  // local mocks) may omit it; the renderer's `deriveParts` shim covers those.
  parts?: MessagePart[];
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
  next_cursor?: string | null;
  profile_complete: boolean;
  thread: ChatThread;
};
