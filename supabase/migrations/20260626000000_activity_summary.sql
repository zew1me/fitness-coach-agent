alter table public.activities
  add column if not exists summary_schema_version integer not null default 1,
  add column if not exists activity_summary jsonb not null default '{}'::jsonb;

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
    'screenshot_extract'
  ));
