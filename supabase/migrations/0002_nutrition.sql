-- athlete_profiles: lightweight nutrition context
alter table public.athlete_profiles
  add column if not exists dietary_restrictions text[] not null default '{}',
  add column if not exists nutrition_notes text;

-- activities: per-session fueling notes
alter table public.activities
  add column if not exists fueling_notes text;
