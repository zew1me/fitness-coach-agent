create table if not exists public.agent_emails (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  to_address text not null,
  from_address text,
  subject text,
  text_body text,
  html_body text,
  raw jsonb not null default '{}'::jsonb,
  consumed_at timestamptz
);

create index if not exists agent_emails_to_created_idx
  on public.agent_emails (to_address, created_at desc);

create index if not exists agent_emails_unconsumed_idx
  on public.agent_emails (to_address, created_at desc)
  where consumed_at is null;

alter table public.agent_emails enable row level security;
