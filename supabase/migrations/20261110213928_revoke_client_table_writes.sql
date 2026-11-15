-- Browser roles (`anon`, `authenticated`) never write application tables directly (#452).
--
-- Every write goes through the FastAPI backend or a Next.js server route, both of which use
-- `service_role` and bypass RLS. The browser and SSR Supabase clients only call
-- `supabase.auth.*`; none of them touches PostgREST tables, RPCs or storage. The earlier
-- "Users can manage their own ..." policies were therefore unused write access, and they only
-- checked a row's own `user_id`, so an athlete could attach a row to another athlete's parent
-- (thread, plan or goal). Removing the write path closes that gap by construction instead of
-- patching each relationship with a parent-ownership check.
--
-- Posture after this migration:
--   * `anon` has no table privileges at all.
--   * `authenticated` can read its own rows where a SELECT policy exists, and nothing else.
--   * A table that ever needs client writes is an explicit exception: it must grant the
--     privilege, add a write policy that also verifies parent ownership, and be listed in
--     CLIENT_WRITABLE_TABLES in tests/python/test_rls_matrix_db.py.
--
-- Privileges and policies are both changed on purpose. Hosted Supabase projects grant browser
-- roles DML (including TRUNCATE, which RLS never covers) on public tables by default, so a
-- policy change alone would leave the first line of defence to RLS only.

-- 1. Privileges: strip browser roles from every existing public table.
do $$
declare
  t record;
begin
  for t in select tablename from pg_tables where schemaname = 'public' loop
    execute format('revoke all on table public.%I from anon', t.tablename);
    execute format(
      'revoke insert, update, delete, truncate on table public.%I from authenticated',
      t.tablename
    );
  end loop;
end
$$;

-- Tables created later by postgres-owned migrations start with no browser access. Hosted
-- projects add anon/authenticated default grants for this role; this removes them.
alter default privileges for role postgres in schema public
  revoke all on tables from anon, authenticated;

-- 2. Policies: owner-managed `for all` policies become read-only.
drop policy if exists "Users can manage their own profile" on public.athlete_profiles;
create policy "Users can read their own profile"
  on public.athlete_profiles for select to authenticated
  using ((select auth.uid())::text = user_id);

drop policy if exists "Users can manage their own thresholds" on public.sport_thresholds;
create policy "Users can read their own thresholds"
  on public.sport_thresholds for select to authenticated
  using ((select auth.uid())::text = user_id);

drop policy if exists "Users can manage their own goals" on public.goals;
create policy "Users can read their own goals"
  on public.goals for select to authenticated
  using ((select auth.uid())::text = user_id);

drop policy if exists "Users can manage their own training plans" on public.training_plans;
create policy "Users can read their own training plans"
  on public.training_plans for select to authenticated
  using ((select auth.uid())::text = user_id);

drop policy if exists "Users can manage their own plan workouts" on public.plan_workouts;
create policy "Users can read their own plan workouts"
  on public.plan_workouts for select to authenticated
  using ((select auth.uid())::text = user_id);

drop policy if exists "Users can manage their own activities" on public.activities;
create policy "Users can read their own activities"
  on public.activities for select to authenticated
  using ((select auth.uid())::text = user_id);

drop policy if exists "Users can manage their own load snapshots" on public.daily_load_snapshots;
create policy "Users can read their own load snapshots"
  on public.daily_load_snapshots for select to authenticated
  using ((select auth.uid())::text = user_id);

drop policy if exists "Users can manage their own recovery logs" on public.recovery_logs;
create policy "Users can read their own recovery logs"
  on public.recovery_logs for select to authenticated
  using ((select auth.uid())::text = user_id);

drop policy if exists "Users can manage their own schedule" on public.schedule_availability;
create policy "Users can read their own schedule"
  on public.schedule_availability for select to authenticated
  using ((select auth.uid())::text = user_id);

drop policy if exists "Users can manage their own schedule overrides" on public.schedule_overrides;
create policy "Users can read their own schedule overrides"
  on public.schedule_overrides for select to authenticated
  using ((select auth.uid())::text = user_id);

drop policy if exists "Users can manage their own chat threads" on public.chat_threads;
create policy "Users can read their own chat threads"
  on public.chat_threads for select to authenticated
  using ((select auth.uid())::text = user_id);

drop policy if exists "Users can manage their own chat messages" on public.chat_messages;
create policy "Users can read their own chat messages"
  on public.chat_messages for select to authenticated
  using ((select auth.uid())::text = user_id);
