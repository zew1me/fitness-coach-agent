-- Phase 1, PR B: restart-safe historical capture. The legacy-insert trigger stays in place.
create table public.activity_sources_backfill_progress (
  singleton boolean primary key default true check (singleton),
  last_created_at timestamptz,
  last_id uuid,
  preflight_at timestamptz,
  processed_rows bigint not null default 0,
  drained_at timestamptz,
  clean_passes integer not null default 0 check (clean_passes between 0 and 2),
  completed_at timestamptz,
  check ((last_created_at is null) = (last_id is null))
);
alter table public.activity_sources_backfill_progress enable row level security;
revoke all on public.activity_sources_backfill_progress from public, anon, authenticated;
grant select, insert, update, delete on public.activity_sources_backfill_progress to service_role;
insert into public.activity_sources_backfill_progress (singleton) values (true);

-- The keyset includes every row, including rows already captured by the shadow writer.
create index activities_backfill_keyset_idx on public.activities (created_at, id);

create function public.activity_sources_backfill_preflight()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_counts jsonb;
begin
  -- Serialize operators, including verification, on the environment-local singleton.
  perform 1 from public.activity_sources_backfill_progress where singleton for update;
  -- Calling the total mapping rejects NULL and every value outside the seven reviewed ones.
  perform m.provider from (select distinct source from public.activities) a
    cross join lateral public.activity_legacy_source_mapping(a.source) m;
  perform public.activity_intervals_external_id(source_file_key)
    from public.activities where source = 'intervals_sync';
  if exists (
    select 1 from public.activities where source = 'intervals_sync'
    group by user_id, public.activity_intervals_external_id(source_file_key)
    having count(*) > 1
  ) then
    raise exception 'Duplicate Intervals external id per user' using errcode = '22023';
  end if;
  select coalesce(jsonb_object_agg(source, n), '{}'::jsonb) into v_counts
    from (select source, count(*) n from public.activities group by source) counts;
  update public.activity_sources_backfill_progress set preflight_at = clock_timestamp()
    where singleton;
  return jsonb_build_object('ok', true, 'source_counts', v_counts);
end;
$$;

create function public.activity_sources_backfill_batch(p_batch_size integer default 500)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_progress public.activity_sources_backfill_progress;
  v_activity public.activities;
  v_expected public.activity_sources;
  v_existing public.activity_sources;
  v_processed integer := 0;
begin
  if p_batch_size is null or p_batch_size < 1 or p_batch_size > 10000 then
    raise exception 'batch size must be between 1 and 10000' using errcode = '22023';
  end if;
  select * into strict v_progress from public.activity_sources_backfill_progress
    where singleton for update;
  if v_progress.preflight_at is null then
    raise exception 'Run activity_sources_backfill_preflight first' using errcode = '22023';
  end if;
  for v_activity in
    select a.* from public.activities a
    where v_progress.last_created_at is null
      or (a.created_at, a.id) > (v_progress.last_created_at, v_progress.last_id)
    order by a.created_at, a.id limit p_batch_size for update
  loop
    -- Never derive evidence from an unlocked snapshot: a preceding UPDATE must be captured.
    v_expected := public.activity_bootstrap_source(v_activity);
    insert into public.activity_sources select (v_expected).* on conflict (id) do nothing;
    if not found then
      select * into strict v_existing from public.activity_sources
        where id = v_expected.id for update;
      -- Timestamps describe the earlier capture, not this attempt. Every other column must
      -- match, including ownership, membership, identity, raw data, fields and NULL content hash.
      if (to_jsonb(v_existing) - 'created_at' - 'updated_at') is distinct from
         (to_jsonb(v_expected) - 'created_at' - 'updated_at') then
        raise exception 'Backfill source conflict for activity %', v_activity.id
          using errcode = '22023';
      end if;
    end if;
    v_progress.last_created_at := v_activity.created_at;
    v_progress.last_id := v_activity.id;
    v_processed := v_processed + 1;
  end loop;
  -- Inserts and checkpoint are one transaction. Even failure here rolls back every source.
  update public.activity_sources_backfill_progress set
    last_created_at = v_progress.last_created_at,
    last_id = v_progress.last_id,
    processed_rows = processed_rows + v_processed,
    drained_at = case when v_processed = 0 then clock_timestamp() else null end,
    clean_passes = case when v_processed = 0 then clean_passes else 0 end,
    completed_at = case when v_processed = 0 then completed_at else null end
  where singleton;
  return jsonb_build_object('processed', v_processed, 'drained', v_processed = 0,
    'last_created_at', v_progress.last_created_at, 'last_id', v_progress.last_id);
end;
$$;

create function public.activity_sources_backfill_verify()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_progress public.activity_sources_backfill_progress;
  v_hole public.activities;
  v_holes bigint;
  v_live_errors bigint;
  v_capture_errors bigint;
begin
  select * into strict v_progress from public.activity_sources_backfill_progress
    where singleton for update;
  perform public.activity_sources_backfill_preflight();
  -- No cursor predicate or LIMIT: an old/backdated insert cannot hide behind the checkpoint.
  select count(*) into v_holes from public.activities a
    left join public.activity_sources s on s.id = a.id where s.id is null;
  select count(*) into v_live_errors from (
    select a.id from public.activities a
    left join public.activity_sources s on s.activity_id = a.id and s.retired_at is null
    group by a.id having count(s.id) <> 1
  ) invalid;
  -- Round-trip the captured fields, not today's mutable projection. A legacy UPDATE after
  -- the batch lock is later projection drift; Phase 3 will give that drift provenance.
  select count(*) into v_capture_errors
  from public.activities a join public.activity_sources s on s.id = a.id
  cross join lateral public.activity_legacy_source_mapping(a.source) m
  cross join lateral jsonb_populate_record(null::public.activities, s.fields) captured
  where s.user_id is distinct from a.user_id
    or s.activity_id is distinct from a.id or s.origin_activity_id is distinct from a.id
    or s.retired_at is not null or s.content_hash is not null
    or s.provider is distinct from m.provider or s.ingest_format is distinct from m.ingest_format
    or s.object_key is distinct from a.source_file_key
    or s.external_id is distinct from case when a.source = 'intervals_sync'
         then public.activity_intervals_external_id(a.source_file_key) end
    or s.fields is distinct from public.activity_projection_fields(captured)
    or s.recorded_at is distinct from captured.started_at
    or s.payload_fingerprint is distinct from public.activity_payload_fingerprint(
         captured.sport, captured.started_at, captured.duration_seconds, captured.distance_meters);

  if v_holes > 0 then
    select a.* into v_hole from public.activities a
      left join public.activity_sources s on s.id = a.id where s.id is null
      order by a.created_at, a.id limit 1;
    -- Store the predecessor, so the next strictly-greater batch includes the earliest hole.
    select a.created_at, a.id into v_progress.last_created_at, v_progress.last_id
      from public.activities a where (a.created_at, a.id) < (v_hole.created_at, v_hole.id)
      order by a.created_at desc, a.id desc limit 1;
    update public.activity_sources_backfill_progress set
      last_created_at = v_progress.last_created_at, last_id = v_progress.last_id,
      drained_at = null, clean_passes = 0, completed_at = null where singleton;
  elsif v_live_errors > 0 or v_capture_errors > 0 or v_progress.drained_at is null then
    update public.activity_sources_backfill_progress set clean_passes = 0, completed_at = null
      where singleton;
  else
    update public.activity_sources_backfill_progress set
      clean_passes = least(clean_passes + 1, 2),
      completed_at = case when clean_passes >= 1 then clock_timestamp() else null end
      where singleton;
  end if;
  select * into strict v_progress from public.activity_sources_backfill_progress where singleton;
  return jsonb_build_object('holes', v_holes, 'live_source_errors', v_live_errors,
    'capture_errors', v_capture_errors, 'drained', v_progress.drained_at is not null,
    'clean_passes', v_progress.clean_passes, 'complete', v_progress.completed_at is not null);
end;
$$;

-- RPCs live in public for PostgREST, with execution explicitly restricted to service_role.
revoke all on function public.activity_sources_backfill_preflight() from public, anon, authenticated;
revoke all on function public.activity_sources_backfill_batch(integer) from public, anon, authenticated;
revoke all on function public.activity_sources_backfill_verify() from public, anon, authenticated;
grant execute on function public.activity_sources_backfill_preflight() to service_role;
grant execute on function public.activity_sources_backfill_batch(integer) to service_role;
grant execute on function public.activity_sources_backfill_verify() to service_role;
