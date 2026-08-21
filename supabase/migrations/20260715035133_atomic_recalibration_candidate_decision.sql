-- Claim a pending recalibration candidate and, when accepted, replace the
-- athlete's active sport threshold in the same database transaction.
--
-- Locking the candidate row makes the pending status the single-winner claim:
-- a concurrent caller waits, then observes that the first caller already
-- changed the status and receives a null result without writing a threshold.
create or replace function public.decide_recalibration_candidate_atomic(
  p_user_id text,
  p_candidate_id uuid,
  p_status text,
  p_threshold jsonb default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  claimed_candidate public.threshold_recalibration_candidates%rowtype;
  decided_candidate public.threshold_recalibration_candidates%rowtype;
  saved_threshold public.sport_thresholds%rowtype;
  decision_time timestamptz := now();
begin
  if p_status not in ('accepted', 'kept_current', 'manual_entered') then
    raise exception 'Invalid recalibration candidate status: %', p_status
      using errcode = '22023';
  end if;
  if p_status in ('accepted', 'manual_entered') and p_threshold is null then
    raise exception 'A threshold is required for recalibration status %', p_status
      using errcode = '22023';
  end if;
  if p_status = 'kept_current' and p_threshold is not null then
    raise exception 'kept_current cannot persist a threshold'
      using errcode = '22023';
  end if;

  select *
  into claimed_candidate
  from public.threshold_recalibration_candidates
  where id = p_candidate_id
    and user_id = p_user_id
    and status = 'pending'
  for update;
  if not found then
    return null;
  end if;

  if p_threshold is not null then
    if nullif(p_threshold->>'user_id', '') is distinct from claimed_candidate.user_id
      or nullif(p_threshold->>'sport', '') is distinct from claimed_candidate.sport then
      raise exception 'Threshold user_id and sport must match the recalibration candidate'
        using errcode = '22023';
    end if;

    update public.sport_thresholds
    set superseded_at = decision_time
    where user_id = claimed_candidate.user_id
      and sport = claimed_candidate.sport
      and superseded_at is null;

    insert into public.sport_thresholds (
      id,
      user_id,
      sport,
      lt1_power_watts,
      lt1_pace_sec_per_km,
      lt1_hr_bpm,
      lt2_power_watts,
      lt2_pace_sec_per_km,
      lt2_hr_bpm,
      css_sec_per_100,
      zones,
      estimation_method,
      estimation_source,
      confidence,
      source,
      effective_from
    )
    values (
      coalesce(nullif(p_threshold->>'id', '')::uuid, gen_random_uuid()),
      claimed_candidate.user_id,
      claimed_candidate.sport,
      nullif(p_threshold->>'lt1_power_watts', '')::integer,
      nullif(p_threshold->>'lt1_pace_sec_per_km', '')::integer,
      nullif(p_threshold->>'lt1_hr_bpm', '')::integer,
      nullif(p_threshold->>'lt2_power_watts', '')::integer,
      nullif(p_threshold->>'lt2_pace_sec_per_km', '')::integer,
      nullif(p_threshold->>'lt2_hr_bpm', '')::integer,
      nullif(p_threshold->>'css_sec_per_100', '')::integer,
      coalesce(p_threshold->'zones', '[]'::jsonb),
      coalesce(nullif(p_threshold->>'estimation_method', ''), 'manual'),
      nullif(p_threshold->>'estimation_source', ''),
      coalesce(nullif(p_threshold->>'confidence', ''), 'low'),
      nullif(p_threshold->>'source', ''),
      coalesce(nullif(p_threshold->>'effective_from', '')::date, current_date)
    )
    returning * into saved_threshold;
  end if;

  update public.threshold_recalibration_candidates
  set
    status = p_status,
    manual_threshold = case
      when p_status = 'manual_entered' then to_jsonb(saved_threshold)
      else null
    end,
    decided_at = decision_time
  where id = claimed_candidate.id
  returning * into decided_candidate;

  return jsonb_build_object(
    'candidate', to_jsonb(decided_candidate),
    'threshold', case
      when saved_threshold.id is null then 'null'::jsonb
      else to_jsonb(saved_threshold)
    end
  );
end;
$$;

revoke all on function public.decide_recalibration_candidate_atomic(text, uuid, text, jsonb)
from public, anon, authenticated;
grant execute on function public.decide_recalibration_candidate_atomic(text, uuid, text, jsonb)
to service_role;
