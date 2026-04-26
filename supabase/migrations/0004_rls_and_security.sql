-- Security hardening: RLS + function search_path fix
-- Closes: https://github.com/zew1me/fitness-coach-agent/issues/112
--         https://github.com/zew1me/fitness-coach-agent/issues/111

-- ============================================================
-- Fix mutable search_path on set_updated_at (WARN #111)
-- ============================================================
create or replace function public.set_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

-- ============================================================
-- Enable RLS on all public tables (ERROR #112)
-- The Python backend connects with service_role key and bypasses
-- RLS automatically. These policies protect PostgREST / anon access.
-- ============================================================

-- athlete_profiles
alter table public.athlete_profiles enable row level security;
create policy "Users can manage their own profile"
  on public.athlete_profiles
  for all
  using (auth.uid()::text = user_id)
  with check (auth.uid()::text = user_id);

-- sport_thresholds
alter table public.sport_thresholds enable row level security;
create policy "Users can manage their own thresholds"
  on public.sport_thresholds
  for all
  using (auth.uid()::text = user_id)
  with check (auth.uid()::text = user_id);

-- goals
alter table public.goals enable row level security;
create policy "Users can manage their own goals"
  on public.goals
  for all
  using (auth.uid()::text = user_id)
  with check (auth.uid()::text = user_id);

-- training_plans
alter table public.training_plans enable row level security;
create policy "Users can manage their own training plans"
  on public.training_plans
  for all
  using (auth.uid()::text = user_id)
  with check (auth.uid()::text = user_id);

-- plan_workouts
alter table public.plan_workouts enable row level security;
create policy "Users can manage their own plan workouts"
  on public.plan_workouts
  for all
  using (auth.uid()::text = user_id)
  with check (auth.uid()::text = user_id);

-- activities
alter table public.activities enable row level security;
create policy "Users can manage their own activities"
  on public.activities
  for all
  using (auth.uid()::text = user_id)
  with check (auth.uid()::text = user_id);

-- daily_load_snapshots
alter table public.daily_load_snapshots enable row level security;
create policy "Users can manage their own load snapshots"
  on public.daily_load_snapshots
  for all
  using (auth.uid()::text = user_id)
  with check (auth.uid()::text = user_id);

-- recovery_logs
alter table public.recovery_logs enable row level security;
create policy "Users can manage their own recovery logs"
  on public.recovery_logs
  for all
  using (auth.uid()::text = user_id)
  with check (auth.uid()::text = user_id);

-- schedule_availability
alter table public.schedule_availability enable row level security;
create policy "Users can manage their own schedule"
  on public.schedule_availability
  for all
  using (auth.uid()::text = user_id)
  with check (auth.uid()::text = user_id);

-- schedule_overrides
alter table public.schedule_overrides enable row level security;
create policy "Users can manage their own schedule overrides"
  on public.schedule_overrides
  for all
  using (auth.uid()::text = user_id)
  with check (auth.uid()::text = user_id);

-- oauth_grants: user_id is a text Supabase auth UUID
alter table public.oauth_grants enable row level security;
create policy "Users can view their own OAuth grants"
  on public.oauth_grants
  for select
  using (auth.uid()::text = user_id);

-- oauth_authorization_codes: short-lived; no direct user access needed
alter table public.oauth_authorization_codes enable row level security;
create policy "Users can view their own authorization codes"
  on public.oauth_authorization_codes
  for select
  using (auth.uid()::text = user_id);

-- oauth_refresh_tokens: no direct user access needed (server-side only)
alter table public.oauth_refresh_tokens enable row level security;
create policy "Users can view their own refresh tokens"
  on public.oauth_refresh_tokens
  for select
  using (auth.uid()::text = user_id);

-- chat_threads
alter table public.chat_threads enable row level security;
create policy "Users can manage their own chat threads"
  on public.chat_threads
  for all
  using (auth.uid()::text = user_id)
  with check (auth.uid()::text = user_id);

-- chat_messages
alter table public.chat_messages enable row level security;
create policy "Users can manage their own chat messages"
  on public.chat_messages
  for all
  using (auth.uid()::text = user_id)
  with check (auth.uid()::text = user_id);

-- chat_attachments
alter table public.chat_attachments enable row level security;
create policy "Users can manage their own chat attachments"
  on public.chat_attachments
  for all
  using (auth.uid()::text = user_id)
  with check (auth.uid()::text = user_id);
