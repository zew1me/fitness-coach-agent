-- Private Agents SDK replay state. The user-visible transcript remains in
-- chat_messages and is never rewritten by compaction.
create table public.chat_model_states (
  thread_id uuid primary key references public.chat_threads(id) on delete cascade,
  user_id text not null unique,
  items jsonb not null default '[]'::jsonb,
  coaching_memory jsonb not null default '[]'::jsonb,
  compaction_metadata jsonb not null default '{}'::jsonb,
  schema_version integer not null default 1 check (schema_version > 0),
  version bigint not null default 0 check (version >= 0),
  lease_id text,
  lease_expires_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create trigger chat_model_states_set_updated_at
before update on public.chat_model_states
for each row execute function public.set_updated_at();

create index chat_model_states_lease_expiry_idx
  on public.chat_model_states (lease_expires_at)
  where lease_id is not null;
