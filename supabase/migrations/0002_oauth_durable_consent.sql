create table if not exists public.oauth_grants (
  id uuid primary key,
  user_id text not null,
  client_id text not null,
  redirect_uri text not null,
  scopes text[] not null default '{}'::text[],
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  revoked_at timestamptz
);

create unique index if not exists oauth_grants_user_client_redirect_active_idx
  on public.oauth_grants (user_id, client_id, redirect_uri)
  where revoked_at is null;

create table if not exists public.oauth_authorization_codes (
  id uuid primary key,
  grant_id uuid not null references public.oauth_grants(id) on delete cascade,
  user_id text not null,
  client_id text not null,
  redirect_uri text not null,
  scopes text[] not null default '{}'::text[],
  code_challenge text not null,
  code_challenge_method text not null,
  token_hash text not null unique,
  expires_at timestamptz not null,
  consumed_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists oauth_authorization_codes_grant_idx
  on public.oauth_authorization_codes (grant_id, created_at desc);

create table if not exists public.oauth_refresh_tokens (
  id uuid primary key,
  grant_id uuid not null references public.oauth_grants(id) on delete cascade,
  user_id text not null,
  client_id text not null,
  scopes text[] not null default '{}'::text[],
  token_hash text not null unique,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  rotated_from_id uuid references public.oauth_refresh_tokens(id) on delete set null
);

create index if not exists oauth_refresh_tokens_grant_idx
  on public.oauth_refresh_tokens (grant_id, created_at desc);

drop trigger if exists oauth_grants_set_updated_at on public.oauth_grants;
create trigger oauth_grants_set_updated_at
before update on public.oauth_grants
for each row
execute function public.set_updated_at();
