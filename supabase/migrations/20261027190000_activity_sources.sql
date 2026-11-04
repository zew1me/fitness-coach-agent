-- Activity sources: expand migration and shadow writer (issue #533, PR A).
--
-- Phase 1 of docs/ACTIVITY_MATERIALIZATION_DESIGN.md (parent #397, umbrella #445).
-- This migration is the "expand" step of an ordered expand / dual-write / contract
-- cutover. It adds the storage for per-input activity evidence and starts capturing it
-- for every new activity, without changing any presentation behaviour:
--
--   * activity_ingest_formats  - reference table holding the format allow-list and the
--                                fidelity rank of each format (rank lives here, never on
--                                a source row).
--   * activity_sources         - one row per ingested input. Evidence columns are
--                                immutable (enforced by trigger); only activity_id and
--                                retired_at may change.
--   * load_rebuild_pending     - invalidation markers for training-load rebuilds. Created
--                                now because later phases need it; nothing writes it yet.
--   * activities               - new projection columns (all defaulted) plus the
--                                redundant unique (id, user_id) that composite foreign
--                                keys target.
--   * a temporary AFTER INSERT trigger on activities that writes a bootstrap source for
--     any legacy direct insert, so the one-source invariant holds during the window in
--     which old application code is still deployed.
--   * create_activity_with_source() - the shadow writer: inserts an activity and its
--     bootstrap source atomically and tells the trigger not to double-capture.
--
-- Deliberately NOT in this migration: the resumable backfill (PR B), revoking direct
-- INSERT on activities and dropping the temporary trigger (PR C, gated on telemetry),
-- content hashing and exact-duplicate rejection (Phase 2). Shadow sources have a NULL
-- content_hash, so nothing here can reject an upload.

-- ============================================================
-- Fidelity reference table
-- ============================================================
create table public.activity_ingest_formats (
  ingest_format text primary key,
  fidelity_rank integer not null unique,
  created_at timestamptz not null default timezone('utc', now())
);

insert into public.activity_ingest_formats (ingest_format, fidelity_rank) values
  ('athlete_override', 0),
  ('fit',              1),
  ('tcx',              2),
  ('gpx',              3),
  ('intervals_api',    4),
  ('text',             5),
  ('screenshot',       6),
  ('manual',           7);

alter table public.activity_ingest_formats enable row level security;

-- Reference data, not athlete data: readable by any signed-in role, changed only by
-- migration.
create policy "Ingest formats are readable"
  on public.activity_ingest_formats
  for select
  to authenticated, service_role
  using (true);

revoke all on table public.activity_ingest_formats from public, anon, authenticated, service_role;
grant select on table public.activity_ingest_formats to authenticated, service_role;

-- ============================================================
-- activities: composite-FK target and projection metadata
-- ============================================================
-- Redundant to the primary key on purpose: it is the target that the composite
-- (id, user_id) foreign keys below reference, which makes a cross-user reference
-- unrepresentable rather than merely discouraged.
alter table public.activities
  add constraint activities_id_user_id_key unique (id, user_id);

alter table public.activities
  add column source_count integer not null default 1,
  add column field_provenance jsonb not null default '{}'::jsonb,
  add column presentation_state text not null default 'active'
    constraint activities_presentation_state_check
    check (presentation_state in ('active', 'superseded')),
  add column superseded_by_activity_id uuid,
  add column materialized_at timestamptz;

-- Composite so one athlete's activity cannot be superseded by another's. The column list
-- on ON DELETE SET NULL (PostgreSQL 15+) nulls only the pointer: a plain SET NULL would
-- also null user_id, which is NOT NULL and would abort the delete. There is no CHECK that
-- couples presentation_state to the pointer, because it would fire during that SET NULL.
alter table public.activities
  add constraint activities_superseded_by_same_user
  foreign key (superseded_by_activity_id, user_id)
  references public.activities (id, user_id)
  on delete set null (superseded_by_activity_id);

-- ============================================================
-- activity_sources
-- ============================================================
create table public.activity_sources (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.athlete_profiles(user_id) on delete cascade,

  -- Composite FKs make "this source belongs to the same athlete as its activity" a
  -- database invariant. They deliberately have no ON UPDATE CASCADE and no ON DELETE
  -- SET NULL: both are UPDATEs and would trip the evidence guard below.
  activity_id uuid not null,
  origin_activity_id uuid not null,
  foreign key (activity_id, user_id)
    references public.activities (id, user_id) on delete cascade,
  foreign key (origin_activity_id, user_id)
    references public.activities (id, user_id) on delete cascade,

  provider text not null check (provider in (
    'garmin', 'intervals', 'strava', 'wahoo', 'coros', 'polar', 'suunto', 'athlete', 'unknown'
  )),
  ingest_format text not null references public.activity_ingest_formats(ingest_format),
  external_id text,
  object_key text,
  content_hash text,
  payload_fingerprint text,
  fields jsonb not null default '{}'::jsonb,
  raw_extraction jsonb,
  recorded_at timestamptz,
  retired_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

-- Byte identity only. Not scoped by provider or ingest_format: both are inferred labels
-- and no inferred field belongs in a key that can reject an upload.
create unique index activity_sources_content_hash_idx
  on public.activity_sources (user_id, content_hash)
  where content_hash is not null and retired_at is null;

-- Provider-issued identity. 'unknown' is excluded because an identifier without a
-- namespace is not authoritative.
create unique index activity_sources_external_id_idx
  on public.activity_sources (user_id, provider, external_id)
  where external_id is not null
    and provider <> 'unknown'
    and retired_at is null;

-- Deliberately NOT unique: a fingerprint is candidate evidence and must never reject.
create index activity_sources_fingerprint_idx
  on public.activity_sources (user_id, payload_fingerprint)
  where payload_fingerprint is not null and retired_at is null;

create index activity_sources_activity_idx
  on public.activity_sources (activity_id) where retired_at is null;

-- The composite foreign keys cascade on activity delete, and the referential-integrity
-- lookup has no retired_at predicate, so the partial index above cannot serve it.
create index activity_sources_activity_user_idx
  on public.activity_sources (activity_id, user_id);
create index activity_sources_origin_activity_user_idx
  on public.activity_sources (origin_activity_id, user_id);

-- At most one live athlete override per activity, so a writer that forgets to retire the
-- previous override fails loudly instead of silently resurrecting an older edit.
create unique index activity_sources_one_live_override_idx
  on public.activity_sources (activity_id)
  where ingest_format = 'athlete_override' and retired_at is null;

-- Evidence is what the ingested input said; only membership (activity_id) and lifecycle
-- (retired_at) may move. Subtracting the mutable keys rather than listing the immutable
-- ones means a column added later is immutable by default, so forgetting to update this
-- guard fails closed. The trigger also binds security definer RPCs, which is the point.
create or replace function public.activity_sources_reject_evidence_update()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if (to_jsonb(new) - 'activity_id' - 'retired_at' - 'updated_at')
     is distinct from
     (to_jsonb(old) - 'activity_id' - 'retired_at' - 'updated_at') then
    raise exception 'activity_sources evidence fields are immutable'
      using errcode = '22023';
  end if;
  return new;
end;
$$;

create trigger activity_sources_evidence_immutable
before update on public.activity_sources
for each row execute function public.activity_sources_reject_evidence_update();

create trigger activity_sources_set_updated_at
before update on public.activity_sources
for each row execute function public.set_updated_at();

alter table public.activity_sources enable row level security;

create policy "Users can read their own activity sources"
  on public.activity_sources
  for select
  to authenticated
  using ((select auth.uid())::text = user_id);

-- Athletes may read their evidence; every write goes through service-role RPCs.
revoke all on table public.activity_sources from public, anon, authenticated;
grant select on table public.activity_sources to authenticated;
grant select, insert, update, delete on table public.activity_sources to service_role;

-- ============================================================
-- load_rebuild_pending (unused until a later phase)
-- ============================================================
create table public.load_rebuild_pending (
  user_id text not null references public.athlete_profiles(user_id) on delete cascade,
  sport text,                 -- null = the aggregate series, as in daily_load_snapshots
  pending_from date not null,
  updated_at timestamptz not null default timezone('utc', now())
);

create unique index load_rebuild_pending_key_idx
  on public.load_rebuild_pending (user_id, sport) nulls not distinct;

alter table public.load_rebuild_pending enable row level security;

create policy "Users can read their own pending load rebuilds"
  on public.load_rebuild_pending
  for select
  to authenticated
  using ((select auth.uid())::text = user_id);

revoke all on table public.load_rebuild_pending from public, anon, authenticated;
grant select on table public.load_rebuild_pending to authenticated;
grant select, insert, update, delete on table public.load_rebuild_pending to service_role;

-- ============================================================
-- Shared helpers
-- The temporary trigger, create_activity_with_source() and the PR B backfill all derive
-- bootstrap evidence through these functions, so the three writers cannot disagree.
-- ============================================================

-- Total mapping from the legacy activities.source vocabulary. There is deliberately no
-- default branch: a newly permitted source value must get a reviewed mapping, not
-- silently become 'unknown'. 'file_upload' is intentionally absent (#462).
create or replace function public.activity_legacy_source_mapping(p_source text)
returns table (provider text, ingest_format text)
language plpgsql
immutable
set search_path = ''
as $$
begin
  case p_source
    when 'manual' then
      provider := 'athlete'; ingest_format := 'manual';
    when 'text_extract' then
      provider := 'athlete'; ingest_format := 'text';
    when 'gpx_upload' then
      provider := 'unknown'; ingest_format := 'gpx';
    when 'fit_upload' then
      provider := 'unknown'; ingest_format := 'fit';
    when 'tcx_upload' then
      provider := 'unknown'; ingest_format := 'tcx';
    when 'screenshot_extract' then
      provider := 'unknown'; ingest_format := 'screenshot';
    when 'intervals_sync' then
      provider := 'intervals'; ingest_format := 'intervals_api';
    else
      raise exception 'No activity source mapping for activities.source %', p_source
        using errcode = '22023';
  end case;
  return next;
end;
$$;

-- The provider activity id inside an 'intervals:{id}' key. Raises on any other shape,
-- because an Intervals source without its external id would lose its identity.
create or replace function public.activity_intervals_external_id(p_source_file_key text)
returns text
language plpgsql
immutable
set search_path = ''
as $$
declare
  v_external_id text;
begin
  v_external_id := substring(p_source_file_key from '^intervals:(.+)$');
  if v_external_id is null then
    raise exception 'Malformed intervals source_file_key %', p_source_file_key
      using errcode = '22023';
  end if;
  return v_external_id;
end;
$$;

-- Candidate-matching hash: sport, start to the second, duration, and distance rounded to
-- 10 m. Elevation is excluded because providers disagree by tens of metres. NULL unless
-- there is a start instant and at least one positive metric. Never a rejection key.
create or replace function public.activity_payload_fingerprint(
  p_sport text,
  p_started_at timestamptz,
  p_duration_seconds integer,
  p_distance_meters double precision
)
returns text
language sql
stable
set search_path = ''
as $$
  select case
    when p_started_at is not null
      and (coalesce(p_duration_seconds, 0) > 0 or coalesce(p_distance_meters, 0) > 0)
    then encode(
      sha256(convert_to(
        concat_ws(
          '|',
          coalesce(p_sport, ''),
          (extract(epoch from date_trunc('second', p_started_at)))::bigint::text,
          coalesce(p_duration_seconds::text, ''),
          coalesce((round(p_distance_meters / 10.0))::bigint::text, '')
        ),
        'UTF8'
      )),
      'hex'
    )
  end
$$;

-- The activity's own metric columns, captured at their current values.
create or replace function public.activity_projection_fields(p_activity public.activities)
returns jsonb
language sql
stable
set search_path = ''
as $$
  -- started_at is rendered in UTC explicitly: a timestamptz would otherwise serialize in
  -- the session time zone, and PR B compares this output against stored fields.
  select jsonb_build_object(
    'sport', p_activity.sport,
    'activity_date', p_activity.activity_date,
    'started_at', to_char(p_activity.started_at at time zone 'UTC',
                          'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'duration_seconds', p_activity.duration_seconds,
    'distance_meters', p_activity.distance_meters,
    'elevation_gain_meters', p_activity.elevation_gain_meters,
    'avg_hr_bpm', p_activity.avg_hr_bpm,
    'max_hr_bpm', p_activity.max_hr_bpm,
    'avg_power_watts', p_activity.avg_power_watts,
    'normalized_power_watts', p_activity.normalized_power_watts,
    'avg_pace_sec_per_km', p_activity.avg_pace_sec_per_km,
    'avg_cadence_rpm', p_activity.avg_cadence_rpm,
    'zone_distribution', p_activity.zone_distribution,
    'tss', p_activity.tss,
    'intensity_factor', p_activity.intensity_factor,
    'rpe', p_activity.rpe,
    'athlete_notes', p_activity.athlete_notes,
    'fatigue_notes', p_activity.fatigue_notes,
    'fueling_notes', p_activity.fueling_notes
  )
$$;

-- The bootstrap-grade source for an activity row: deterministic id (= activity id),
-- origin = activity, NULL content_hash.
create or replace function public.activity_bootstrap_source(p_activity public.activities)
returns public.activity_sources
language plpgsql
stable
set search_path = ''
as $$
declare
  v_mapping record;
  v_source public.activity_sources;
begin
  select * into v_mapping from public.activity_legacy_source_mapping(p_activity.source);

  v_source.id := p_activity.id;
  v_source.user_id := p_activity.user_id;
  v_source.activity_id := p_activity.id;
  v_source.origin_activity_id := p_activity.id;
  v_source.provider := v_mapping.provider;
  v_source.ingest_format := v_mapping.ingest_format;
  v_source.external_id := case
    when p_activity.source = 'intervals_sync'
      then public.activity_intervals_external_id(p_activity.source_file_key)
  end;
  v_source.object_key := p_activity.source_file_key;
  v_source.content_hash := null;
  v_source.payload_fingerprint := public.activity_payload_fingerprint(
    p_activity.sport,
    p_activity.started_at,
    p_activity.duration_seconds,
    p_activity.distance_meters
  );
  v_source.fields := public.activity_projection_fields(p_activity);
  v_source.raw_extraction := p_activity.raw_extraction;
  v_source.recorded_at := p_activity.started_at;
  v_source.retired_at := null;
  v_source.created_at := timezone('utc', now());
  v_source.updated_at := v_source.created_at;
  return v_source;
end;
$$;

-- ============================================================
-- Temporary legacy-insert trigger
-- ============================================================
-- Covers the window in which application code that predates create_activity_with_source
-- still inserts into activities directly. It is dropped by the contract migration
-- (PR C), and only after telemetry shows the LOG line below has stopped appearing.
-- Never drop it merely because a deploy was requested.
--
-- security definer: browser roles can still insert into activities through RLS but have
-- no INSERT on activity_sources, and a legacy insert must still be captured.
create or replace function public.activities_capture_legacy_source()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  -- create_activity_with_source() sets this transaction-local marker and writes the
  -- source itself. The marker is cutover coordination, not an authorization boundary.
  if coalesce(current_setting('app.activity_source_bootstrap', true), '') = 'on' then
    return null;
  end if;

  -- Fixed text on purpose: telemetry greps for exactly this string to prove that legacy
  -- direct inserts have stopped before the contract migration is allowed to merge.
  raise log 'activity_sources: legacy activities insert captured by bootstrap trigger';

  -- FROM-clause call, not `(f(new)).*`: the latter re-evaluates the function per column.
  insert into public.activity_sources
  select * from public.activity_bootstrap_source(new);
  return null;
end;
$$;

create trigger activities_capture_legacy_source
after insert on public.activities
for each row execute function public.activities_capture_legacy_source();

-- ============================================================
-- Shadow writer
-- ============================================================
-- p_activity is the activity projection as JSON (the keys of the Activity model that map
-- to activities columns). p_source optionally carries the writer's view of the evidence
-- it can see: {"object_key": ..., "external_id": ...}. The bootstrap source is always
-- derived by the shared helpers; a supplied value that disagrees with the derived one
-- raises 22023, and unknown keys are rejected so a Phase 2 field cannot be silently
-- dropped by an older database function.
--
-- Returns the inserted activity, or NULL when an Intervals activity with the same
-- (user_id, intervals_source_file_key) already exists (no source is written). That keeps
-- the idempotent-sync behaviour of the upsert it replaces.
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
    if v_key not in ('object_key', 'external_id') then
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

  insert into public.activity_sources values (v_source.*);

  perform set_config('app.activity_source_bootstrap', 'off', true);
  return v_activity;
end;
$$;

-- Helpers are plain functions, not a client surface; only the RPC and the server-side
-- repositories (service_role) may call them.
revoke all on function public.activity_legacy_source_mapping(text)
  from public, anon, authenticated;
revoke all on function public.activity_intervals_external_id(text)
  from public, anon, authenticated;
revoke all on function public.activity_payload_fingerprint(
  text, timestamptz, integer, double precision
) from public, anon, authenticated;
revoke all on function public.activity_projection_fields(public.activities)
  from public, anon, authenticated;
revoke all on function public.activity_bootstrap_source(public.activities)
  from public, anon, authenticated;
revoke all on function public.activities_capture_legacy_source()
  from public, anon, authenticated;
revoke all on function public.create_activity_with_source(text, jsonb, jsonb)
  from public, anon, authenticated;

grant execute on function public.activity_legacy_source_mapping(text) to service_role;
grant execute on function public.activity_intervals_external_id(text) to service_role;
grant execute on function public.activity_payload_fingerprint(
  text, timestamptz, integer, double precision
) to service_role;
grant execute on function public.activity_projection_fields(public.activities) to service_role;
grant execute on function public.activity_bootstrap_source(public.activities) to service_role;
grant execute on function public.create_activity_with_source(text, jsonb, jsonb)
  to service_role;
