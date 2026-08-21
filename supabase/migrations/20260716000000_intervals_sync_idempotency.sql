-- Keep Intervals imports idempotent without constraining other import sources:
-- a ZIP can legitimately create several activities with the same source_file_key.
alter table public.activities
  add column intervals_source_file_key text generated always as (
    case when source = 'intervals_sync' then source_file_key end
  ) stored;

alter table public.activities
  add constraint activities_intervals_source_file_key_unique
  unique (user_id, intervals_source_file_key);
