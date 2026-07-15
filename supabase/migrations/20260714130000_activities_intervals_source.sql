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
    'intervals_sync'
  ));
