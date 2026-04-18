-- Sport-agnostic endurance coaching schema (full rip-and-replace)
-- Drop all old tables first
drop table if exists public.chat_attachments cascade;
drop table if exists public.chat_messages cascade;
drop table if exists public.chat_threads cascade;
drop table if exists public.oauth_refresh_tokens cascade;
drop table if exists public.oauth_authorization_codes cascade;
drop table if exists public.oauth_grants cascade;
drop table if exists public.check_ins cascade;
drop table if exists public.plan_workouts cascade;
drop table if exists public.training_plans cascade;
drop table if exists public.activities cascade;
drop table if exists public.daily_load_snapshots cascade;
drop table if exists public.recovery_logs cascade;
drop table if exists public.schedule_overrides cascade;
drop table if exists public.schedule_availability cascade;
drop table if exists public.goals cascade;
drop table if exists public.sport_thresholds cascade;
drop table if exists public.athlete_profiles cascade;

create extension if not exists pgcrypto;

-- ============================================================
-- Utility: updated_at trigger function
-- ============================================================
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

-- ============================================================
-- Athlete profiles
-- ============================================================
create table public.athlete_profiles (
  user_id text primary key,
  display_name text,
  biological_sex text check (biological_sex in ('male', 'female', 'not_specified')),
  hormone_status text check (hormone_status in ('endogenous', 'hrt_estrogen', 'hrt_testosterone', 'not_specified')),
  birth_date date,
  weight_kg double precision,
  height_cm double precision,
  resting_hr_bpm integer,
  max_hr_bpm integer,
  primary_sports text[] not null default '{}',
  weekly_available_hours double precision,
  coaching_state text not null default 'onboarding'
    check (coaching_state in ('onboarding', 'calibrating', 'active', 'paused')),
  specialization_pct integer not null default 80
    check (specialization_pct between 0 and 100),
  onboarding_collected jsonb not null default '{}'::jsonb,
  constraints text[] not null default '{}',
  injuries_rehab text[] not null default '{}',
  notes text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create trigger athlete_profiles_set_updated_at
before update on public.athlete_profiles
for each row execute function public.set_updated_at();

-- ============================================================
-- Per-sport thresholds (LT1 / LT2) with history
-- ============================================================
create table public.sport_thresholds (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.athlete_profiles(user_id) on delete cascade,
  sport text not null check (sport in ('cycling', 'running', 'swimming', 'rowing', 'hiking', 'general')),

  -- LT1 (aerobic threshold)
  lt1_power_watts integer,
  lt1_pace_sec_per_km integer,
  lt1_hr_bpm integer,

  -- LT2 (anaerobic threshold / FTP / LTHR)
  lt2_power_watts integer,
  lt2_pace_sec_per_km integer,
  lt2_hr_bpm integer,

  zones jsonb not null default '[]'::jsonb,

  estimation_method text not null default 'manual'
    check (estimation_method in ('manual', 'race_time', 'field_test', 'hrv_drift', 'model_estimate')),
  estimation_source text,
  confidence text not null default 'low'
    check (confidence in ('low', 'medium', 'high')),

  effective_from date not null default current_date,
  superseded_at timestamptz,

  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create index sport_thresholds_user_sport_active_idx
  on public.sport_thresholds (user_id, sport, effective_from desc)
  where superseded_at is null;

create trigger sport_thresholds_set_updated_at
before update on public.sport_thresholds
for each row execute function public.set_updated_at();

-- ============================================================
-- Goals
-- ============================================================
create table public.goals (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.athlete_profiles(user_id) on delete cascade,

  goal_type text not null check (goal_type in ('event', 'mountain', 'improvement', 'maintenance', 'secondary')),
  sport text,
  title text not null,
  description text,
  target_date date,

  target_ctl double precision,
  target_metric_name text,
  target_metric_value double precision,

  -- Course / terrain spec (event and mountain goals)
  course_distance_meters double precision,
  course_elevation_gain_meters double precision,
  course_avg_grade_pct double precision,
  course_max_grade_pct double precision,
  course_profile jsonb,

  -- Improvement goal spec
  improvement_metric text,
  improvement_target_value double precision,
  improvement_baseline_value double precision,

  priority integer not null default 1 check (priority between 1 and 5),
  status text not null default 'active' check (status in ('active', 'completed', 'abandoned')),

  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create index goals_user_status_idx on public.goals (user_id, status)
  where status = 'active';

create trigger goals_set_updated_at
before update on public.goals
for each row execute function public.set_updated_at();

-- ============================================================
-- Training plans
-- ============================================================
create table public.training_plans (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.athlete_profiles(user_id) on delete cascade,

  title text not null,
  plan_type text not null check (plan_type in ('full_cycle', 'mesocycle', 'weekly', 'adjustment')),
  status text not null default 'active' check (status in ('draft', 'active', 'completed', 'superseded')),

  start_date date not null,
  end_date date not null,
  target_goal_id uuid references public.goals(id) on delete set null,

  phases jsonb not null default '[]'::jsonb,
  generation_context jsonb,
  weekly_tss_target double precision,
  weekly_hours_target double precision,

  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create index training_plans_user_status_idx
  on public.training_plans (user_id, status)
  where status = 'active';

create trigger training_plans_set_updated_at
before update on public.training_plans
for each row execute function public.set_updated_at();

-- ============================================================
-- Plan workouts (prescribed)
-- ============================================================
create table public.plan_workouts (
  id uuid primary key default gen_random_uuid(),
  plan_id uuid not null references public.training_plans(id) on delete cascade,
  user_id text not null,

  workout_date date not null,
  day_of_week integer not null check (day_of_week between 0 and 6),
  week_number integer not null,
  phase_name text,

  sport text not null,
  title text not null,
  description text,
  workout_type text not null check (workout_type in (
    'recovery', 'endurance', 'tempo', 'sweet_spot', 'threshold', 'vo2max',
    'anaerobic', 'sprint', 'race', 'strength', 'mobility', 'rest',
    'long_run', 'long_ride', 'brick', 'interval', 'fartlek', 'hill_repeats'
  )),

  target_duration_minutes integer,
  target_distance_meters double precision,
  target_tss double precision,
  target_intensity_factor double precision,
  zone_targets jsonb,
  intervals jsonb,

  status text not null default 'scheduled'
    check (status in ('scheduled', 'completed', 'skipped', 'modified')),

  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

-- actual_activity_id added after activities table exists
create index plan_workouts_plan_date_idx on public.plan_workouts (plan_id, workout_date);
create index plan_workouts_user_date_idx on public.plan_workouts (user_id, workout_date);

create trigger plan_workouts_set_updated_at
before update on public.plan_workouts
for each row execute function public.set_updated_at();

-- ============================================================
-- Activities (structured workout log)
-- ============================================================
create table public.activities (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.athlete_profiles(user_id) on delete cascade,
  sport text not null,
  activity_date date not null,
  started_at timestamptz,

  duration_seconds integer,
  distance_meters double precision,
  elevation_gain_meters double precision,
  avg_hr_bpm integer,
  max_hr_bpm integer,
  avg_power_watts integer,
  normalized_power_watts integer,
  avg_pace_sec_per_km integer,
  avg_cadence_rpm integer,

  tss double precision,
  intensity_factor double precision,
  zone_distribution jsonb,

  rpe integer check (rpe between 1 and 10),
  athlete_notes text,
  fatigue_notes text,

  source text not null default 'manual'
    check (source in ('manual', 'text_extract', 'gpx_upload', 'fit_upload', 'screenshot_extract')),
  source_file_key text,
  raw_extraction jsonb,

  planned_workout_id uuid references public.plan_workouts(id) on delete set null,

  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create index activities_user_date_idx on public.activities (user_id, activity_date desc);
create index activities_user_sport_idx on public.activities (user_id, sport, activity_date desc);

create trigger activities_set_updated_at
before update on public.activities
for each row execute function public.set_updated_at();

-- Add the back-reference from plan_workouts → activities
alter table public.plan_workouts
  add column actual_activity_id uuid references public.activities(id) on delete set null;

-- ============================================================
-- Daily load snapshots (CTL / ATL / TSB)
-- ============================================================
create table public.daily_load_snapshots (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.athlete_profiles(user_id) on delete cascade,
  snapshot_date date not null,
  sport text,  -- null = aggregate across all sports

  daily_tss double precision not null default 0,
  ctl double precision not null default 0,
  atl double precision not null default 0,
  tsb double precision not null default 0,

  created_at timestamptz not null default timezone('utc', now()),

  unique (user_id, snapshot_date, sport)
);

create index daily_load_user_date_idx
  on public.daily_load_snapshots (user_id, snapshot_date desc);

-- ============================================================
-- Recovery logs (daily wellness)
-- ============================================================
create table public.recovery_logs (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.athlete_profiles(user_id) on delete cascade,
  log_date date not null,

  sleep_duration_hours double precision,
  sleep_score integer,
  sleep_consistency_pct double precision,
  hrv_ms double precision,
  resting_hr_bpm integer,
  body_battery integer,
  stress_score integer,
  subjective_energy integer check (subjective_energy between 1 and 5),
  notes text,

  source text not null default 'manual'
    check (source in ('manual', 'garmin_api', 'apple_health', 'screenshot_extract')),

  created_at timestamptz not null default timezone('utc', now()),

  unique (user_id, log_date)
);

create index recovery_logs_user_date_idx
  on public.recovery_logs (user_id, log_date desc);

-- ============================================================
-- Schedule availability
-- ============================================================
create table public.schedule_availability (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.athlete_profiles(user_id) on delete cascade,
  weekly_pattern jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (user_id)
);

create trigger schedule_availability_set_updated_at
before update on public.schedule_availability
for each row execute function public.set_updated_at();

create table public.schedule_overrides (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.athlete_profiles(user_id) on delete cascade,
  override_date date not null,
  available boolean not null default false,
  max_hours double precision,
  reason text,
  created_at timestamptz not null default timezone('utc', now()),
  unique (user_id, override_date)
);

-- ============================================================
-- OAuth (unchanged from original)
-- ============================================================
create table public.oauth_grants (
  id uuid primary key,
  user_id text not null,
  client_id text not null,
  redirect_uri text not null,
  scopes text[] not null default '{}'::text[],
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  revoked_at timestamptz
);

create unique index oauth_grants_user_client_redirect_active_idx
  on public.oauth_grants (user_id, client_id, redirect_uri)
  where revoked_at is null;

create trigger oauth_grants_set_updated_at
before update on public.oauth_grants
for each row execute function public.set_updated_at();

create table public.oauth_authorization_codes (
  id uuid primary key,
  grant_id uuid not null references public.oauth_grants(id) on delete cascade,
  user_id text not null,
  client_id text not null,
  redirect_uri text not null,
  scopes text[] not null default '{}'::text[],
  code_challenge text not null,
  code_challenge_method text not null,
  token_hash text not null unique,
  expires_at timestamptz not null,
  consumed_at timestamptz,
  created_at timestamptz not null default now()
);

create index oauth_authorization_codes_grant_idx
  on public.oauth_authorization_codes (grant_id, created_at desc);

create table public.oauth_refresh_tokens (
  id uuid primary key,
  grant_id uuid not null references public.oauth_grants(id) on delete cascade,
  user_id text not null,
  client_id text not null,
  scopes text[] not null default '{}'::text[],
  token_hash text not null unique,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  rotated_from_id uuid references public.oauth_refresh_tokens(id) on delete set null
);

create index oauth_refresh_tokens_grant_idx
  on public.oauth_refresh_tokens (grant_id, created_at desc);

-- ============================================================
-- Chat (unchanged from original)
-- ============================================================
create table public.chat_threads (
  id uuid primary key,
  user_id text not null unique,
  state jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create trigger chat_threads_set_updated_at
before update on public.chat_threads
for each row execute function public.set_updated_at();

create table public.chat_messages (
  id uuid primary key,
  thread_id uuid not null references public.chat_threads(id) on delete cascade,
  user_id text not null,
  role text not null check (role in ('user', 'assistant')),
  content text not null default '',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now())
);

create index chat_messages_thread_created_at_idx
  on public.chat_messages (thread_id, created_at asc);

create table public.chat_attachments (
  id uuid primary key,
  message_id uuid not null references public.chat_messages(id) on delete cascade,
  user_id text not null,
  filename text not null,
  content_type text not null,
  object_key text not null,
  public_url text,
  created_at timestamptz not null default timezone('utc', now())
);

create index chat_attachments_message_created_at_idx
  on public.chat_attachments (message_id, created_at asc);
