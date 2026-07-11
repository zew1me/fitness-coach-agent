create table public.intervals_connections (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.athlete_profiles(user_id) on delete cascade,
  intervals_athlete_id text not null,
  intervals_athlete_name text,
  scopes text[] not null default '{}'::text[],
  access_token_ciphertext text not null,
  token_type text not null default 'Bearer',
  connected_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  revoked_at timestamptz
);

create unique index intervals_connections_user_active_idx
  on public.intervals_connections (user_id)
  where revoked_at is null;

create index intervals_connections_user_connected_idx
  on public.intervals_connections (user_id, connected_at desc);

create trigger intervals_connections_set_updated_at
before update on public.intervals_connections
for each row execute function public.set_updated_at();

alter table public.intervals_connections enable row level security;

revoke all on table public.intervals_connections from public;
revoke all on table public.intervals_connections from anon;
revoke all on table public.intervals_connections from authenticated;
grant select, insert, update, delete on table public.intervals_connections to service_role;
