-- Aggregate load has sport = NULL. Ordinary UNIQUE treats those rows as distinct,
-- so ON CONFLICT used to insert a new aggregate snapshot on every rebuild.
begin;

-- Keep cleanup and constraint replacement atomic with respect to concurrent writers.
lock table public.daily_load_snapshots in access exclusive mode;

-- These rows are derived data. Keep the most recently created snapshot for each
-- aggregate key, using id as a deterministic tie-breaker for equal timestamps.
with ranked as (
  select id, row_number() over (
    partition by user_id, snapshot_date
    order by created_at desc, id desc
  ) as position
  from public.daily_load_snapshots
  where sport is null
)
delete from public.daily_load_snapshots as snapshot
using ranked
where snapshot.id = ranked.id and ranked.position > 1;

alter table public.daily_load_snapshots
  drop constraint daily_load_snapshots_user_id_snapshot_date_sport_key,
  add constraint daily_load_snapshots_user_id_snapshot_date_sport_key
    unique nulls not distinct (user_id, snapshot_date, sport);

commit;
