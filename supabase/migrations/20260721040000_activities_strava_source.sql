-- Allow Strava-sourced canonical activities and keep their imports idempotent.

alter table public.activities
  drop constraint if exists activities_source_check;

alter table public.activities
  add constraint activities_source_check
  check (source in (
    'manual',
    'text_extract',
    'gpx_upload',
    'fit_upload',
    'tcx_upload',
    'screenshot_extract',
    'intervals_sync',
    'strava_sync'
  )) not valid;

alter table public.activities
  validate constraint activities_source_check;

-- Strava-only generated key, independent from intervals_source_file_key, so a
-- Strava re-sync upserts the same canonical row. Other import sources (e.g. a ZIP
-- yielding several activities with a shared source_file_key) stay unconstrained.
alter table public.activities
  add column strava_source_file_key text generated always as (
    case when source = 'strava_sync' then source_file_key end
  ) stored;

alter table public.activities
  add constraint activities_strava_source_file_key_unique
  unique (user_id, strava_source_file_key);
