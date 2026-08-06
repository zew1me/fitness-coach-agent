-- Atomically clear both sides of an existing plan-workout/activity match.
--
-- Date corrections persist the activity's other edited fields first while retaining
-- the existing link. This RPC then makes the bidirectional unlink all-or-nothing
-- before the application attempts a best-effort match on the corrected date.

create or replace function public.unlink_plan_workout_from_activity(
  p_user_id text,
  p_plan_workout_id uuid,
  p_activity_id uuid
)
returns public.activities
language plpgsql
security definer
set search_path = public
as $$
declare
  current_workout public.plan_workouts%rowtype;
  updated_activity public.activities%rowtype;
begin
  -- Match the lock order used by the other plan-workout RPCs: workout, then activity.
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

  select *
  into updated_activity
  from public.activities
  where id = p_activity_id
    and user_id = p_user_id
  for update;
  if not found then
    raise exception 'Activity % not found for user %', p_activity_id, p_user_id
      using errcode = 'P0002';
  end if;

  -- A retried request that already completed is safe and returns current state.
  if current_workout.actual_activity_id is null
    and updated_activity.planned_workout_id is null
  then
    return updated_activity;
  end if;

  -- Never clear a link that was concurrently reassigned to a different record.
  if current_workout.actual_activity_id is distinct from p_activity_id
    or updated_activity.planned_workout_id is distinct from p_plan_workout_id
  then
    raise exception 'Plan workout % and activity % are not linked',
      p_plan_workout_id, p_activity_id
      using errcode = '22023';
  end if;

  update public.plan_workouts
  set
    status = 'scheduled',
    actual_activity_id = null,
    completion_source = null
  where id = p_plan_workout_id
    and user_id = p_user_id;

  update public.activities
  set planned_workout_id = null
  where id = p_activity_id
    and user_id = p_user_id
  returning * into updated_activity;

  return updated_activity;
end;
$$;

revoke all on function public.unlink_plan_workout_from_activity(text, uuid, uuid)
  from public, anon, authenticated;
grant execute on function public.unlink_plan_workout_from_activity(text, uuid, uuid)
  to service_role;
