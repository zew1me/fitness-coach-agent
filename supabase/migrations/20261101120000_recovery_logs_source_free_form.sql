-- recovery_logs.source records where a row's numbers came from (#428). Wellness data
-- arrives from an open-ended set of devices and apps (Garmin, intervals.icu, Whoop, Oura,
-- Polar, Fitbit, Withings, Eight Sleep, ...), so the column no longer enumerates vendors.
-- It only constrains the shape: a lowercase snake_case slug, which the API normalizes to.
alter table public.recovery_logs
  drop constraint if exists recovery_logs_source_check;

alter table public.recovery_logs
  add constraint recovery_logs_source_check
  check (source ~ '^[a-z0-9]+(_[a-z0-9]+)*$' and char_length(source) <= 64) not valid;

alter table public.recovery_logs
  validate constraint recovery_logs_source_check;
