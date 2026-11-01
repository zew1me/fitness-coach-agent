-- Enforce the plan workout <-> activity link as one-to-one (issue #467).
--
-- The link is stored on both sides (plan_workouts.actual_activity_id and
-- activities.planned_workout_id) and was kept 1:1 only by convention inside the
-- RPCs below. Neither RPC checked whether the target activity was already
-- claimed by a different workout, so linking it a second time credited two
-- workouts for one activity and stranded the first (the unlink RPC refuses a
-- pair whose reverse link moved on). The clear-prior branches also cleared the
-- prior activity's reverse link without checking that it still pointed back at
-- this workout.
--
-- This migration:
--   1. Refuses (SQLSTATE 22023) to link an activity that a different workout
--      already claims, on either side of the link, and writes nothing.
--   2. Clears a prior activity's reverse link only while it still points at
--      this workout.
--   3. Clears the prior reverse link before writing the new one, so the
--      reassignment never holds two rows with the same planned_workout_id.
--   4. Adds partial unique indexes so a second claim is unrepresentable.
--
-- Hosted data was checked on 2026-09-01 (see #467): no workout or activity is
-- claimed twice in preview or production. If a violation appears before this
-- runs, index creation fails loudly and the migration aborts, which is the
-- intended outcome; repair the pair by hand rather than weakening the index.

create or replace function public.match_plan_workout_to_activity(
  p_user_id text,
  p_plan_workout_id uuid,
  p_activity_id uuid,
  p_completion_source text default 'auto_matched'
)
returns public.plan_workouts
language plpgsql
security definer
set search_path = public
as $$
declare
  updated_workout public.plan_workouts%rowtype;
  prior_activity_id uuid;
  target_planned_workout_id uuid;
  claiming_workout_id uuid;
begin
  if p_completion_source not in ('auto_matched', 'athlete_confirmed', 'coach_confirmed') then
    raise exception 'Invalid completion_source: %', p_completion_source
      using errcode = '22023';
  end if;

  select actual_activity_id
  into prior_activity_id
  from public.plan_workouts
  where id = p_plan_workout_id
    and user_id = p_user_id
  for update;
  if not found then
    raise exception 'Plan workout % not found for user %', p_plan_workout_id, p_user_id
      using errcode = 'P0002';
  end if;

  select planned_workout_id
  into target_planned_workout_id
  from public.activities
  where id = p_activity_id
    and user_id = p_user_id
  for update;
  if not found then
    raise exception 'Activity % not found for user %', p_activity_id, p_user_id
      using errcode = 'P0002';
  end if;

  -- Never take an activity away from the workout that already claims it: that
  -- would credit two workouts for one session and strand the first one.
  select id
  into claiming_workout_id
  from public.plan_workouts
  where actual_activity_id = p_activity_id
    and user_id = p_user_id
    and id <> p_plan_workout_id
  limit 1;
  if claiming_workout_id is null
    and target_planned_workout_id is not null
    and target_planned_workout_id <> p_plan_workout_id
  then
    claiming_workout_id := target_planned_workout_id;
  end if;
  if claiming_workout_id is not null then
    raise exception 'Activity % is already linked to plan workout %; unlink it first',
      p_activity_id, claiming_workout_id
      using errcode = '22023';
  end if;

  -- Reassigning this workout to a different activity: clear the old activity's
  -- reverse link first, and only while it still points at this workout.
  if prior_activity_id is not null and prior_activity_id <> p_activity_id then
    update public.activities
    set planned_workout_id = null
    where id = prior_activity_id
      and user_id = p_user_id
      and planned_workout_id = p_plan_workout_id;
  end if;

  update public.plan_workouts
  set
    status = 'completed',
    actual_activity_id = p_activity_id,
    completion_source = p_completion_source
  where id = p_plan_workout_id
    and user_id = p_user_id
  returning * into updated_workout;

  update public.activities
  set planned_workout_id = p_plan_workout_id
  where id = p_activity_id
    and user_id = p_user_id;

  return updated_workout;
end;
$$;

create or replace function public.resolve_plan_workout(
  p_user_id text,
  p_plan_workout_id uuid,
  p_outcome text,
  p_activity_id uuid default null,
  p_source text default 'coach'
)
returns public.plan_workouts
language plpgsql
security definer
set search_path = public
as $$
declare
  current_workout public.plan_workouts%rowtype;
  updated_workout public.plan_workouts%rowtype;
  prior_activity_id uuid;
  target_planned_workout_id uuid;
  claiming_workout_id uuid;
  resolved_completion_source text;
begin
  if p_outcome not in ('completed', 'skipped') then
    raise exception 'Invalid outcome: %', p_outcome
      using errcode = '22023';
  end if;
  if p_source not in ('athlete', 'coach') then
    raise exception 'Invalid source: %', p_source
      using errcode = '22023';
  end if;

  resolved_completion_source := p_source || '_confirmed';

  select *
  into current_workout
  from public.plan_workouts
  where id = p_plan_workout_id
    and user_id = p_user_id
  for update;
  if not found then
    raise exception 'Plan workout % not found for user %', p_plan_workout_id, p_user_id
      using errcode = 'P0002';
  end if;
  prior_activity_id := current_workout.actual_activity_id;

  if p_activity_id is not null then
    select planned_workout_id
    into target_planned_workout_id
    from public.activities
    where id = p_activity_id
      and user_id = p_user_id
    for update;
    if not found then
      raise exception 'Activity % not found for user %', p_activity_id, p_user_id
        using errcode = 'P0002';
    end if;

    -- A skip never links p_activity_id, so only a completion can steal a claim.
    if p_outcome = 'completed' then
      select id
      into claiming_workout_id
      from public.plan_workouts
      where actual_activity_id = p_activity_id
        and user_id = p_user_id
        and id <> p_plan_workout_id
      limit 1;
      if claiming_workout_id is null
        and target_planned_workout_id is not null
        and target_planned_workout_id <> p_plan_workout_id
      then
        claiming_workout_id := target_planned_workout_id;
      end if;
      if claiming_workout_id is not null then
        raise exception 'Activity % is already linked to plan workout %; unlink it first',
          p_activity_id, claiming_workout_id
          using errcode = '22023';
      end if;
    end if;
  end if;

  -- Clear the prior activity's reverse link before writing a new one, and only
  -- while it still points at this workout.
  if prior_activity_id is not null
    and (
      p_outcome = 'skipped'
      or (p_activity_id is not null and prior_activity_id <> p_activity_id)
    )
  then
    update public.activities
    set planned_workout_id = null
    where id = prior_activity_id
      and user_id = p_user_id
      and planned_workout_id = p_plan_workout_id;
  end if;

  update public.plan_workouts
  set
    status = p_outcome,
    completion_source = resolved_completion_source,
    actual_activity_id = case
      when p_outcome = 'skipped' then null
      when p_activity_id is not null then p_activity_id
      else actual_activity_id
    end
  where id = p_plan_workout_id
    and user_id = p_user_id
  returning * into updated_workout;

  -- Only a completion links the activity. (The previous version also wrote this
  -- reverse link on a skip that named an activity, leaving the activity pointing
  -- at a workout whose actual_activity_id had just been cleared.)
  if p_activity_id is not null and p_outcome = 'completed' then
    update public.activities
    set planned_workout_id = p_plan_workout_id
    where id = p_activity_id
      and user_id = p_user_id;
  end if;

  return updated_workout;
end;
$$;

-- create or replace keeps existing grants, but restate them so this migration is
-- self-describing and cannot silently widen access.
revoke all on function public.match_plan_workout_to_activity(text, uuid, uuid, text)
  from public, anon, authenticated;
revoke all on function public.resolve_plan_workout(text, uuid, text, uuid, text)
  from public, anon, authenticated;
grant execute on function public.match_plan_workout_to_activity(text, uuid, uuid, text)
  to service_role;
grant execute on function public.resolve_plan_workout(text, uuid, text, uuid, text)
  to service_role;

-- Both tables are small (hundreds of rows), so plain index creation is fine.
create unique index plan_workouts_actual_activity_unique
  on public.plan_workouts (actual_activity_id)
  where actual_activity_id is not null;

create unique index activities_planned_workout_unique
  on public.activities (planned_workout_id)
  where planned_workout_id is not null;
