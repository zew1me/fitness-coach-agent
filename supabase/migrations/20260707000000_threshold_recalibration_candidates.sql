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

create index if not exists threshold_recalibration_candidates_pending_idx
  on public.threshold_recalibration_candidates (user_id, sport)
  where status = 'pending';

drop trigger if exists threshold_recalibration_candidates_set_updated_at
  on public.threshold_recalibration_candidates;
create trigger threshold_recalibration_candidates_set_updated_at
before update on public.threshold_recalibration_candidates
for each row execute function public.set_updated_at();
