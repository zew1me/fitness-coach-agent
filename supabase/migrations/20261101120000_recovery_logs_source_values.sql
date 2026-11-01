alter table public.recovery_logs
  drop constraint if exists recovery_logs_source_check;

alter table public.recovery_logs
  add constraint recovery_logs_source_check
  check (source in (
    'manual',
    'garmin_api',
    'apple_health',
    'screenshot_extract',
    'garmin_sidecar',
    'intervals_sync'
  )) not valid;

alter table public.recovery_logs
  validate constraint recovery_logs_source_check;
