-- Atomic plan workout/activity link maintenance.
--
-- These functions keep the bidirectional references between plan_workouts and
-- activities consistent inside one Postgres transaction. The FastAPI backend
-- calls them via PostgREST RPC using the service-role key.

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

  perform 1
  from public.activities
  where id = p_activity_id
    and user_id = p_user_id
  for update;
  if not found then
    raise exception 'Activity % not found for user %', p_activity_id, p_user_id
      using errcode = 'P0002';
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

  -- Reassigning this workout to a different activity: clear the old
  -- activity's reverse link so it no longer points at a workout it is
  -- not actually linked from.
  if prior_activity_id is not null and prior_activity_id <> p_activity_id then
    update public.activities
    set planned_workout_id = null
    where id = prior_activity_id
      and user_id = p_user_id;
  end if;

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
    perform 1
    from public.activities
    where id = p_activity_id
      and user_id = p_user_id
    for update;
    if not found then
      raise exception 'Activity % not found for user %', p_activity_id, p_user_id
        using errcode = 'P0002';
    end if;
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

  if p_activity_id is not null then
    update public.activities
    set planned_workout_id = p_plan_workout_id
    where id = p_activity_id
      and user_id = p_user_id;
  end if;

  if prior_activity_id is not null
    and (
      p_outcome = 'skipped'
      or (p_activity_id is not null and prior_activity_id <> p_activity_id)
    )
  then
    update public.activities
    set planned_workout_id = null
    where id = prior_activity_id
      and user_id = p_user_id;
  end if;

  return updated_workout;
end;
$$;

revoke all on function public.match_plan_workout_to_activity(text, uuid, uuid, text)
  from public, anon, authenticated;
revoke all on function public.resolve_plan_workout(text, uuid, text, uuid, text)
  from public, anon, authenticated;

grant execute on function public.match_plan_workout_to_activity(text, uuid, uuid, text)
  to service_role;
grant execute on function public.resolve_plan_workout(text, uuid, text, uuid, text)
  to service_role;
