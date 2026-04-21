-- Fitness threshold model: source of truth, estimation, and temporal versioning
-- Resolves: https://github.com/zew1me/fitness-coach-agent/issues/54

-- ============================================================
-- athlete_profiles: source metadata for non-sport-specific metrics
-- ============================================================
alter table public.athlete_profiles
  add column if not exists max_hr_source text
    check (max_hr_source in ('user', 'file', 'estimated')),
  add column if not exists max_hr_measured_at date,
  add column if not exists max_hr_notes text,
  add column if not exists weight_source text
    check (weight_source in ('user', 'file', 'estimated')),
  add column if not exists weight_measured_at date,
  add column if not exists weight_notes text,
  add column if not exists best_times jsonb not null default '[]'::jsonb;

-- ============================================================
-- sport_thresholds: swim CSS + explicit 3-way source enum
-- ============================================================
alter table public.sport_thresholds
  add column if not exists css_sec_per_100 integer,
  add column if not exists source text
    check (source in ('user', 'file', 'estimated'));
