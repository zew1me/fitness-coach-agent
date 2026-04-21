export type AthleteProfileContext = {
  biological_sex?: string | null;
  coaching_state: "active" | "calibrating" | "onboarding" | "paused" | string;
  dietary_restrictions?: string[];
  display_name?: string | null;
  hormone_status?: string | null;
  nutrition_notes?: string | null;
  primary_sports: string[];
  user_id: string;
  weekly_available_hours?: number | null;
};

export type SportThresholdContext = {
  confidence: string;
  effective_from?: string | null;
  estimation_method?: string | null;
  sport: string;
  lt1_hr_bpm?: number | null;
  lt1_pace_sec_per_km?: number | null;
  lt1_power_watts?: number | null;
  lt2_hr_bpm?: number | null;
  lt2_pace_sec_per_km?: number | null;
  lt2_power_watts?: number | null;
};

export type GoalContext = {
  course_distance_meters?: number | null;
  course_elevation_gain_meters?: number | null;
  goal_type: string;
  id?: string | null;
  priority: number;
  sport?: string | null;
  status: string;
  target_date?: string | null;
  title: string;
  user_id: string;
};

export type LoadSnapshotContext = {
  atl: number;
  ctl: number;
  daily_tss: number;
  snapshot_date: string;
  sport?: string | null;
  tsb: number;
  user_id: string;
};

export type RecoveryLogContext = {
  body_battery?: number | null;
  hrv_ms?: number | null;
  log_date: string;
  resting_hr_bpm?: number | null;
  sleep_score?: number | null;
};

export type ScheduleContext = {
  weekly_pattern: Record<string, unknown>;
};

export type TrainingPlanContext = {
  end_date: string;
  phases: Array<Record<string, unknown>>;
  start_date: string;
  status: string;
  title: string;
};

export type CTLCeilingGuidance = {
  age_bracket: string;
  committed_amateur_ctl: number;
  elite_ctl: number;
  notes: string;
  recovery_week_frequency: string;
  recreational_ctl: number;
};

export type AthleteContextBundle = {
  active_plan: TrainingPlanContext | null;
  computed_age: number | null;
  ctl_ceiling_guidance: CTLCeilingGuidance;
  current_load: LoadSnapshotContext | null;
  goals: GoalContext[];
  profile: AthleteProfileContext;
  recent_recovery: RecoveryLogContext[];
  schedule: ScheduleContext | null;
  thresholds: SportThresholdContext[];
};
