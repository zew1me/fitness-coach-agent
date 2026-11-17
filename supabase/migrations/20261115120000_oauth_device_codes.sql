-- RFC 8628 device authorization grant for the first-party coach CLI (#568).
--
-- One row per `POST /api/oauth/device/code`. The CLI holds the raw device_code; only its
-- SHA-256 hex digest is stored, exactly like oauth_authorization_codes.token_hash and
-- oauth_refresh_tokens.token_hash. The short user_code is typed by the athlete into /device
-- and is unique only among pending rows, so the 8-character space can be reused once a code
-- leaves the pending state. The backend deletes rows that expired more than a day ago.

create table public.oauth_device_codes (
  id uuid primary key,
  device_code_hash text not null unique,
  user_code text not null,
  client_id text not null,
  scopes text[] not null default '{}'::text[],
  status text not null default 'pending'
    constraint oauth_device_codes_status_check
    check (status in ('pending', 'approved', 'denied', 'consumed')),
  -- Set when the athlete approves; null while pending or after a denial.
  user_id text,
  grant_id uuid references public.oauth_grants(id) on delete cascade,
  interval_seconds integer not null default 5
    constraint oauth_device_codes_interval_positive check (interval_seconds > 0),
  expires_at timestamptz not null,
  last_polled_at timestamptz,
  approved_at timestamptz,
  created_at timestamptz not null default now(),
  constraint oauth_device_codes_approval_check
    check (status not in ('approved', 'consumed') or (user_id is not null and grant_id is not null))
);

create unique index oauth_device_codes_pending_user_code_idx
  on public.oauth_device_codes (user_code)
  where status = 'pending';

-- Supports the opportunistic purge of long-expired rows.
create index oauth_device_codes_expires_at_idx
  on public.oauth_device_codes (expires_at);

-- Supports the on-delete-cascade from oauth_grants.
create index oauth_device_codes_grant_idx
  on public.oauth_device_codes (grant_id)
  where grant_id is not null;

-- Server-side only, like oauth_authorization_codes and oauth_refresh_tokens: RLS on with no
-- client policies, so anon and authenticated see and change nothing even where the hosted
-- project's default grants give them table privileges. The backend uses service_role.
alter table public.oauth_device_codes enable row level security;

revoke all on table public.oauth_device_codes from public, anon, authenticated;
grant select, insert, update, delete on table public.oauth_device_codes to service_role;
