-- Per-athlete Strava connection with rotating OAuth tokens.
--
-- Unlike Intervals (a static bearer token), Strava access tokens expire after
-- ~6 hours and every refresh may rotate the refresh token, so both tokens are
-- stored encrypted alongside their expiry. Only service_role touches this table;
-- tokens never reach the browser.
create table public.strava_connections (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.athlete_profiles(user_id) on delete cascade,
  strava_athlete_id bigint not null,
  strava_athlete_name text,
  scopes text[] not null default '{}'::text[],
  access_token_ciphertext text not null,
  refresh_token_ciphertext text not null,
  token_type text not null default 'Bearer',
  expires_at timestamptz not null,
  authorization_version text,
  consented_at timestamptz not null default timezone('utc', now()),
  connected_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  last_sync_at timestamptz,
  revoked_at timestamptz
);

-- Single Player Mode: at most one active connection per athlete.
create unique index strava_connections_user_active_idx
  on public.strava_connections (user_id)
  where revoked_at is null;

create index strava_connections_user_connected_idx
  on public.strava_connections (user_id, connected_at desc);

create trigger strava_connections_set_updated_at
before update on public.strava_connections
for each row execute function public.set_updated_at();

alter table public.strava_connections enable row level security;

revoke all on table public.strava_connections from public;
revoke all on table public.strava_connections from anon;
revoke all on table public.strava_connections from authenticated;
grant select, insert, update, delete on table public.strava_connections to service_role;

-- Replace an athlete's active Strava connection atomically.
--
-- Mirrors the repaired Intervals pattern: a per-user advisory transaction lock
-- serializes concurrent replacements, and the revoke+insert happen in one
-- transaction so a crash can never leave the athlete with zero (or two) active
-- connections racing the partial unique index.
create or replace function public.replace_strava_connection(
  p_user_id text,
  p_strava_athlete_id bigint,
  p_strava_athlete_name text,
  p_scopes text[],
  p_access_token_ciphertext text,
  p_refresh_token_ciphertext text,
  p_token_type text,
  p_expires_at timestamptz,
  p_authorization_version text
)
returns public.strava_connections
language plpgsql
security definer
set search_path = ''
as $$
declare
  new_connection public.strava_connections%rowtype;
  replacement_time timestamptz := now();
begin
  if nullif(p_user_id, '') is null then
    raise exception 'user_id is required to replace a Strava connection'
      using errcode = '22023';
  end if;
  if p_strava_athlete_id is null then
    raise exception 'strava_athlete_id is required to replace a Strava connection'
      using errcode = '22023';
  end if;
  if nullif(p_access_token_ciphertext, '') is null then
    raise exception 'access_token_ciphertext is required to replace a Strava connection'
      using errcode = '22023';
  end if;
  if nullif(p_refresh_token_ciphertext, '') is null then
    raise exception 'refresh_token_ciphertext is required to replace a Strava connection'
      using errcode = '22023';
  end if;
  if p_expires_at is null then
    raise exception 'expires_at is required to replace a Strava connection'
      using errcode = '22023';
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended('strava_connections:' || p_user_id, 0)
  );

  update public.strava_connections
  set
    revoked_at = replacement_time,
    updated_at = replacement_time
  where user_id = p_user_id
    and revoked_at is null;

  insert into public.strava_connections (
    user_id,
    strava_athlete_id,
    strava_athlete_name,
    scopes,
    access_token_ciphertext,
    refresh_token_ciphertext,
    token_type,
    expires_at,
    authorization_version,
    consented_at,
    connected_at,
    updated_at,
    revoked_at
  )
  values (
    p_user_id,
    p_strava_athlete_id,
    p_strava_athlete_name,
    coalesce(p_scopes, '{}'::text[]),
    p_access_token_ciphertext,
    p_refresh_token_ciphertext,
    coalesce(nullif(p_token_type, ''), 'Bearer'),
    p_expires_at,
    p_authorization_version,
    replacement_time,
    replacement_time,
    replacement_time,
    null
  )
  returning * into new_connection;

  return new_connection;
end;
$$;

revoke all on function public.replace_strava_connection(
  text, bigint, text, text[], text, text, text, timestamptz, text
) from public, anon, authenticated;
grant execute on function public.replace_strava_connection(
  text, bigint, text, text[], text, text, text, timestamptz, text
) to service_role;

-- Persist a rotated access/refresh token pair for the active connection.
--
-- Guards against a stale refresh response clobbering a newer rotation with a
-- compare-and-swap on the previously observed expiry: the update only lands when
-- the row still carries the expiry the caller refreshed against. A no-op result
-- signals the caller to reload and use the already-rotated token rather than
-- overwrite it. Defers the full advisory-lock lease (webhook-era concern) in
-- favor of this single-statement CAS, sufficient for user-triggered sync.
create or replace function public.rotate_strava_tokens(
  p_connection_id uuid,
  p_expected_expires_at timestamptz,
  p_access_token_ciphertext text,
  p_refresh_token_ciphertext text,
  p_token_type text,
  p_expires_at timestamptz
)
returns public.strava_connections
language plpgsql
security definer
set search_path = ''
as $$
declare
  rotated public.strava_connections%rowtype;
begin
  update public.strava_connections
  set
    access_token_ciphertext = p_access_token_ciphertext,
    refresh_token_ciphertext = p_refresh_token_ciphertext,
    token_type = coalesce(nullif(p_token_type, ''), 'Bearer'),
    expires_at = p_expires_at,
    updated_at = now()
  where id = p_connection_id
    and revoked_at is null
    and expires_at = p_expected_expires_at
  returning * into rotated;

  return rotated;  -- NULL row when the CAS lost or the connection was revoked
end;
$$;

revoke all on function public.rotate_strava_tokens(
  uuid, timestamptz, text, text, text, timestamptz
) from public, anon, authenticated;
grant execute on function public.rotate_strava_tokens(
  uuid, timestamptz, text, text, text, timestamptz
) to service_role;
