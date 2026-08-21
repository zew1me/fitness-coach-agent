-- Atomic active training plan creation.
--
-- Chat-triggered generation and future unattended daily jobs can race for the
-- same athlete. This RPC serializes plan creation per athlete, supersedes the
-- current active plan, and inserts the replacement active plan in one database
-- transaction.

create or replace function public.create_training_plan_atomic(p_plan jsonb)
returns public.training_plans
language plpgsql
security definer
set search_path = public
as $$
declare
  plan_user_id text;
  inserted_plan public.training_plans%rowtype;
begin
  plan_user_id := nullif(p_plan->>'user_id', '');
  if plan_user_id is null then
    raise exception 'Training plan user_id is required'
      using errcode = '22023';
  end if;

  -- Serialize all active-plan replacement work for a single athlete. The
  -- foreign key requires this profile to exist, so a missing row is a genuine
  -- caller error rather than a case to silently create.
  perform 1
  from public.athlete_profiles
  where user_id = plan_user_id
  for update;
  if not found then
    raise exception 'Athlete profile % not found', plan_user_id
      using errcode = 'P0002';
  end if;

  update public.training_plans
  set status = 'superseded'
  where user_id = plan_user_id
    and status = 'active';

  insert into public.training_plans (
    id,
    user_id,
    title,
    plan_type,
    status,
    start_date,
    end_date,
    target_goal_id,
    phases,
    generation_context,
    weekly_tss_target,
    weekly_hours_target
  )
  values (
    coalesce(nullif(p_plan->>'id', '')::uuid, gen_random_uuid()),
    plan_user_id,
    p_plan->>'title',
    p_plan->>'plan_type',
    'active',
    (p_plan->>'start_date')::date,
    (p_plan->>'end_date')::date,
    nullif(p_plan->>'target_goal_id', '')::uuid,
    coalesce(p_plan->'phases', '[]'::jsonb),
    p_plan->'generation_context',
    nullif(p_plan->>'weekly_tss_target', '')::double precision,
    nullif(p_plan->>'weekly_hours_target', '')::double precision
  )
  returning * into inserted_plan;

  return inserted_plan;
end;
$$;

revoke all on function public.create_training_plan_atomic(jsonb)
from public, anon, authenticated;
grant execute on function public.create_training_plan_atomic(jsonb)
to service_role;
