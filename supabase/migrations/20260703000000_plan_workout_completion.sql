-- Compliance glue (get_compliance_summary): record how a plan workout reached
-- a terminal status so nudge effectiveness can be analyzed later.
alter table public.plan_workouts
  add column if not exists completion_source text
    constraint plan_workouts_completion_source_check
    check (completion_source in ('auto_matched', 'athlete_confirmed', 'coach_confirmed'));

-- Fast lookup of past workouts still awaiting confirmation ("unconfirmed" is
-- derived at read time: workout_date in the past and status still 'scheduled').
-- Plain CREATE INDEX (not CONCURRENTLY): the stable Supabase CLI (≤2.101)
-- wraps migrations in a transaction where CONCURRENTLY is rejected, and this
-- table is small enough that the build lock is negligible.
create index if not exists plan_workouts_user_scheduled_idx
  on public.plan_workouts (user_id, workout_date desc)
  where status = 'scheduled';
