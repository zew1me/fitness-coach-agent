-- #591: transactional, payload-free change notices for out-of-chat ingestion.
-- These describe committed records, not uploads attempted or a successful whole batch.
create table public.coach_data_changes (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.athlete_profiles(user_id) on delete cascade,
  record_id uuid not null,
  data_type text not null check (data_type in ('activity', 'recovery')),
  source text not null check (source in (
    'fit_upload', 'gpx_upload', 'tcx_upload', 'intervals_sync',
    'manual', 'text_extract', 'screenshot_extract', 'garmin', 'other'
  )),
  record_date date not null,
  operation text not null check (operation in ('created', 'updated')),
  created_at timestamptz not null default now()
);
create index coach_data_changes_owner_order
  on public.coach_data_changes (user_id, created_at, id);
alter table public.coach_data_changes enable row level security;
-- No browser read/write policies: these are private application-context events.
revoke all on public.coach_data_changes from public, anon, authenticated;
grant all on public.coach_data_changes to service_role;

create function public.capture_coach_data_change()
returns trigger language plpgsql security definer set search_path = '' as $$
declare
  v_type text;
  v_date date;
  v_source text;
begin
  if tg_table_name = 'activities' then
    -- Plan matching, derived summaries, and timestamp-only writes are not imports.
    if tg_op = 'UPDATE' and
       (to_jsonb(new) - array['updated_at', 'planned_workout_id', 'activity_summary',
         'summary_schema_version', 'field_provenance', 'materialized_at', 'source_count'])
       is not distinct from
       (to_jsonb(old) - array['updated_at', 'planned_workout_id', 'activity_summary',
         'summary_schema_version', 'field_provenance', 'materialized_at', 'source_count']) then
      return new;
    end if;
    v_type := 'activity';
    v_date := new.activity_date;
  else
    -- Re-pushing identical wellness values is a no-op even though updated_at moves.
    -- The legacy wellness upsert also replaces id; identity here is (user, log_date).
    if tg_op = 'UPDATE' and (to_jsonb(new) - array['id', 'updated_at'])
       is not distinct from (to_jsonb(old) - array['id', 'updated_at']) then
      return new;
    end if;
    v_type := 'recovery';
    v_date := new.log_date;
  end if;
  -- Recovery sources are athlete-authored free text. Never copy them into instructions.
  v_source := case when new.source in (
    'fit_upload', 'gpx_upload', 'tcx_upload', 'intervals_sync', 'manual',
    'text_extract', 'screenshot_extract', 'garmin'
  ) then new.source else 'other' end;
  insert into public.coach_data_changes
    (user_id, record_id, data_type, source, record_date, operation)
  values (new.user_id, new.id, v_type, v_source, v_date,
          case when tg_op = 'INSERT' then 'created' else 'updated' end);
  return new;
end;
$$;
revoke all on function public.capture_coach_data_change() from public, anon, authenticated;

create trigger activities_coach_data_change after insert or update on public.activities
  for each row execute function public.capture_coach_data_change();
create trigger recovery_coach_data_change after insert or update on public.recovery_logs
  for each row execute function public.capture_coach_data_change();

-- Acknowledgements live in the successfully persisted assistant reply, not model items.
-- This expression index keeps per-event receipt lookups out of a transcript scan.
create index chat_messages_data_change_receipts on public.chat_messages
  using gin ((metadata -> 'data_change_event_ids'))
  where role = 'assistant' and metadata ->> 'finish_reason' = 'stop';

create function public.get_coach_data_changes(p_user_id text, p_thread_id uuid)
returns jsonb language sql stable security invoker set search_path = '' as $$
  with pending as materialized (
    select e.* from public.coach_data_changes e
    where e.user_id = p_user_id
      and not exists (
        select 1 from public.chat_messages m
        join public.chat_threads t on t.id = m.thread_id and t.user_id = p_user_id
        where m.user_id = p_user_id and m.thread_id = p_thread_id and m.role = 'assistant'
          and m.metadata ->> 'finish_reason' = 'stop'
          and (m.metadata -> 'data_change_event_ids') @> jsonb_build_array(e.id::text)
      )
    order by e.created_at, e.id limit 257
  ), page as materialized (
    select * from pending order by created_at, id limit 256
  ), records as (
    select data_type, source,
      case when data_type = 'recovery' then record_date::text else record_id::text end as record_key,
      min(record_date) as start_date,
      max(record_date) as end_date, bool_or(operation = 'created') as was_created
    from page group by data_type, source, record_key
  ), summaries as (
    select data_type, source, min(start_date) as start_date, max(end_date) as end_date,
      count(*) filter (where was_created) as created_records,
      count(*) filter (where not was_created) as updated_records
    from records group by data_type, source
  )
  select jsonb_build_object(
    'event_ids', coalesce((select jsonb_agg(id order by created_at, id) from page), '[]'::jsonb),
    'summaries', coalesce((select jsonb_agg(to_jsonb(s) order by data_type, source)
      from summaries s), '[]'::jsonb),
    'has_more', (select count(*) > 256 from pending)
  );
$$;
revoke all on function public.get_coach_data_changes(text, uuid) from public, anon, authenticated;
grant execute on function public.get_coach_data_changes(text, uuid) to service_role;
