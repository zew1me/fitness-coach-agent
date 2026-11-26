-- Activity materialization (#397), Phase 2a: exact byte identity for file uploads.
-- CREATE OR REPLACE preserves the signature and existing service-role-only ACL.
-- Old writers may omit the hash. Leave legacy Intervals identity and the bootstrap
-- trigger in place. Historical evidence is immutable and is not assigned a hash.

create or replace function public.create_activity_with_source(
  p_user_id text,
  p_activity jsonb,
  p_source jsonb default '{}'::jsonb
)
returns public.activities
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_record public.activities;
  v_activity public.activities;
  v_source public.activity_sources;
  v_key text;
begin
  if p_user_id is null or p_activity is null or jsonb_typeof(p_activity) <> 'object' then
    raise exception 'p_user_id and an object p_activity are required'
      using errcode = '22023';
  end if;
  if p_activity ->> 'user_id' is distinct from p_user_id then
    raise exception 'p_activity.user_id must equal p_user_id'
      using errcode = '22023';
  end if;
  if p_source is null then
    p_source := '{}'::jsonb;
  end if;
  if jsonb_typeof(p_source) <> 'object' then
    raise exception 'p_source must be an object'
      using errcode = '22023';
  end if;
  for v_key in select jsonb_object_keys(p_source) loop
    if v_key not in ('object_key', 'external_id', 'content_hash') then
      raise exception 'Unsupported p_source key %', v_key
        using errcode = '22023';
    end if;
  end loop;

  -- Fail closed on any key the insert below would silently drop. A new activities column
  -- must be assigned to a writer (and added here) rather than discarded; the RPC-owned
  -- projection metadata columns and the generated intervals key are deliberately absent.
  for v_key in select jsonb_object_keys(p_activity) loop
    if v_key <> all (array[
      'id', 'user_id', 'sport', 'activity_date', 'started_at',
      'duration_seconds', 'distance_meters', 'elevation_gain_meters',
      'avg_hr_bpm', 'max_hr_bpm', 'avg_power_watts', 'normalized_power_watts',
      'avg_pace_sec_per_km', 'avg_cadence_rpm',
      'tss', 'intensity_factor', 'zone_distribution',
      'rpe', 'athlete_notes', 'fatigue_notes', 'fueling_notes',
      'source', 'source_file_key', 'raw_extraction',
      'summary_schema_version', 'activity_summary', 'planned_workout_id'
    ]) then
      raise exception 'Unsupported p_activity key %', v_key
        using errcode = '22023';
    end if;
  end loop;

  v_record := jsonb_populate_record(null::public.activities, p_activity);
  -- No silent default: an activity without a source has no reviewed bootstrap mapping.
  if v_record.source is null then
    raise exception 'p_activity.source is required'
      using errcode = '22023';
  end if;

  -- Only recorded file uploads carry a server-computed SHA-256 of the original
  -- bytes. Require the canonical lowercase form so one digest has one index key.
  if p_source ? 'content_hash' and (
    jsonb_typeof(p_source -> 'content_hash') <> 'string'
    or (p_source ->> 'content_hash') !~ '^[0-9a-f]{64}$'
    or v_record.source not in ('fit_upload', 'gpx_upload', 'tcx_upload')
  ) then
    raise exception 'p_source.content_hash must be a lowercase SHA-256 for a file upload'
      using errcode = '22023';
  end if;

  -- Tell the temporary legacy trigger that this transaction writes its own source.
  perform set_config('app.activity_source_bootstrap', 'on', true);

  -- The column list is explicit: intervals_source_file_key is generated and the
  -- projection metadata columns keep their defaults until a later phase owns them.
  insert into public.activities (
    id, user_id, sport, activity_date, started_at,
    duration_seconds, distance_meters, elevation_gain_meters,
    avg_hr_bpm, max_hr_bpm, avg_power_watts, normalized_power_watts,
    avg_pace_sec_per_km, avg_cadence_rpm,
    tss, intensity_factor, zone_distribution,
    rpe, athlete_notes, fatigue_notes, fueling_notes,
    source, source_file_key, raw_extraction,
    summary_schema_version, activity_summary, planned_workout_id
  ) values (
    coalesce(v_record.id, gen_random_uuid()), v_record.user_id, v_record.sport,
    v_record.activity_date, v_record.started_at,
    v_record.duration_seconds, v_record.distance_meters, v_record.elevation_gain_meters,
    v_record.avg_hr_bpm, v_record.max_hr_bpm, v_record.avg_power_watts,
    v_record.normalized_power_watts,
    v_record.avg_pace_sec_per_km, v_record.avg_cadence_rpm,
    v_record.tss, v_record.intensity_factor, v_record.zone_distribution,
    v_record.rpe, v_record.athlete_notes, v_record.fatigue_notes, v_record.fueling_notes,
    v_record.source, v_record.source_file_key, v_record.raw_extraction,
    coalesce(v_record.summary_schema_version, 1),
    coalesce(v_record.activity_summary, '{}'::jsonb), v_record.planned_workout_id
  )
  on conflict (user_id, intervals_source_file_key) do nothing
  returning * into v_activity;

  if not found then
    perform set_config('app.activity_source_bootstrap', 'off', true);
    return null;
  end if;

  v_source := public.activity_bootstrap_source(v_activity);

  if p_source ? 'object_key'
     and p_source ->> 'object_key' is distinct from v_source.object_key then
    raise exception 'p_source.object_key disagrees with the activity'
      using errcode = '22023';
  end if;
  if p_source ? 'external_id'
     and p_source ->> 'external_id' is distinct from v_source.external_id then
    raise exception 'p_source.external_id disagrees with the activity'
      using errcode = '22023';
  end if;

  -- The existing partial unique index serializes equal live hashes per athlete.
  -- If it rejects, the whole RPC rolls back, including the activity and any notices.
  v_source.content_hash := p_source ->> 'content_hash';
  insert into public.activity_sources values (v_source.*);

  perform set_config('app.activity_source_bootstrap', 'off', true);
  return v_activity;
end;
$$;
