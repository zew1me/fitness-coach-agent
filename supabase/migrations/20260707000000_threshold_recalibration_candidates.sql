-- Threshold recalibration candidates require athlete confirmation before persistence.

create table if not exists public.threshold_recalibration_candidates (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.athlete_profiles(user_id) on delete cascade,
  sport text not null check (sport in ('cycling', 'running', 'swimming', 'rowing', 'hiking', 'general')),
  status text not null default 'pending'
    check (status in ('pending', 'accepted', 'kept_current', 'manual_entered', 'superseded')),
  confidence text not null check (confidence in ('low', 'medium', 'high')),
  evidence_activity_id text,
  evidence_reason text,
  explanation text not null,
  candidate_threshold jsonb not null,
  manual_threshold jsonb,
  generated_at timestamptz not null default timezone('utc', now()),
  decided_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create index if not exists threshold_recalibration_candidates_user_sport_generated_idx
  on public.threshold_recalibration_candidates (user_id, sport, generated_at desc);

-- Unique (not just indexed) so at most one pending candidate can exist per
-- athlete/sport even if a caller ever bypasses create_recalibration_candidate_atomic.
create unique index if not exists threshold_recalibration_candidates_pending_idx
  on public.threshold_recalibration_candidates (user_id, sport)
  where status = 'pending';

drop trigger if exists threshold_recalibration_candidates_set_updated_at
  on public.threshold_recalibration_candidates;
create trigger threshold_recalibration_candidates_set_updated_at
before update on public.threshold_recalibration_candidates
for each row execute function public.set_updated_at();

-- Atomic candidate creation.
--
-- create_recalibration_candidate previously superseded the existing pending
-- candidate and inserted the replacement as two separate round trips, so
-- concurrent recalibration requests for the same athlete/sport could each
-- see the prior candidate as pending and both insert, leaving two pending
-- rows. Lock the athlete profile row (mirrors create_training_plan_atomic)
-- to serialize this per athlete and do both writes in one transaction.
create or replace function public.create_recalibration_candidate_atomic(p_candidate jsonb)
returns public.threshold_recalibration_candidates
language plpgsql
security definer
set search_path = public
as $$
declare
  candidate_user_id text;
  candidate_sport text;
  inserted_candidate public.threshold_recalibration_candidates%rowtype;
begin
  candidate_user_id := nullif(p_candidate->>'user_id', '');
  candidate_sport := nullif(p_candidate->>'sport', '');
  if candidate_user_id is null or candidate_sport is null then
    raise exception 'Recalibration candidate user_id and sport are required'
      using errcode = '22023';
  end if;

  perform 1
  from public.athlete_profiles
  where user_id = candidate_user_id
  for update;
  if not found then
    raise exception 'Athlete profile % not found', candidate_user_id
      using errcode = 'P0002';
  end if;

  update public.threshold_recalibration_candidates
  set status = 'superseded', decided_at = timezone('utc', now())
  where user_id = candidate_user_id
    and sport = candidate_sport
    and status = 'pending';

  insert into public.threshold_recalibration_candidates (
    id,
    user_id,
    sport,
    status,
    confidence,
    evidence_activity_id,
    evidence_reason,
    explanation,
    candidate_threshold,
    manual_threshold,
    generated_at
  )
  values (
    coalesce(nullif(p_candidate->>'id', '')::uuid, gen_random_uuid()),
    candidate_user_id,
    candidate_sport,
    'pending',
    p_candidate->>'confidence',
    nullif(p_candidate->>'evidence_activity_id', ''),
    nullif(p_candidate->>'evidence_reason', ''),
    p_candidate->>'explanation',
    p_candidate->'candidate_threshold',
    p_candidate->'manual_threshold',
    coalesce((p_candidate->>'generated_at')::timestamptz, timezone('utc', now()))
  )
  returning * into inserted_candidate;

  return inserted_candidate;
end;
$$;

revoke all on function public.create_recalibration_candidate_atomic(jsonb)
from public, anon, authenticated;
grant execute on function public.create_recalibration_candidate_atomic(jsonb)
to service_role;
