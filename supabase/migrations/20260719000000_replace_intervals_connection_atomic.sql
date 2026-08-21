-- Replace an athlete's active Intervals.icu connection atomically.
--
-- The repo previously revoked the active connection and inserted its
-- replacement as two independent PostgREST calls. A crash between them left
-- the athlete with zero active connections, and two interleaved
-- revoke-then-insert sequences raced the partial unique index
-- (intervals_connections_user_active_idx), failing one caller with a unique
-- violation. Doing both steps inside one function makes the swap transactional,
-- and the per-user advisory lock serializes concurrent replacements so the
-- later committer cleanly becomes the active connection.
create or replace function public.replace_intervals_connection(
  p_user_id text,
  p_intervals_athlete_id text,
  p_intervals_athlete_name text,
  p_scopes text[],
  p_access_token_ciphertext text,
  p_token_type text
)
returns public.intervals_connections
language plpgsql
security definer
set search_path = ''
as $$
declare
  new_connection public.intervals_connections%rowtype;
  replacement_time timestamptz := now();
begin
  if nullif(p_user_id, '') is null then
    raise exception 'user_id is required to replace an Intervals connection'
      using errcode = '22023';
  end if;
  if nullif(p_intervals_athlete_id, '') is null then
    raise exception 'intervals_athlete_id is required to replace an Intervals connection'
      using errcode = '22023';
  end if;
  if nullif(p_access_token_ciphertext, '') is null then
    raise exception 'access_token_ciphertext is required to replace an Intervals connection'
      using errcode = '22023';
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended('intervals_connections:' || p_user_id, 0)
  );

  update public.intervals_connections
  set
    revoked_at = replacement_time,
    updated_at = replacement_time
  where user_id = p_user_id
    and revoked_at is null;

  insert into public.intervals_connections (
    user_id,
    intervals_athlete_id,
    intervals_athlete_name,
    scopes,
    access_token_ciphertext,
    token_type,
    connected_at,
    updated_at,
    revoked_at
  )
  values (
    p_user_id,
    p_intervals_athlete_id,
    p_intervals_athlete_name,
    coalesce(p_scopes, '{}'::text[]),
    p_access_token_ciphertext,
    coalesce(nullif(p_token_type, ''), 'Bearer'),
    replacement_time,
    replacement_time,
    null
  )
  returning * into new_connection;

  return new_connection;
end;
$$;

revoke all on function public.replace_intervals_connection(
  text, text, text, text[], text, text
) from public, anon, authenticated;
grant execute on function public.replace_intervals_connection(
  text, text, text, text[], text, text
) to service_role;
