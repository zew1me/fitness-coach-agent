create table if not exists public.chat_threads (
  id uuid primary key,
  user_id text not null unique,
  state jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.chat_messages (
  id uuid primary key,
  thread_id uuid not null references public.chat_threads(id) on delete cascade,
  user_id text not null,
  role text not null check (role in ('user', 'assistant')),
  content text not null default '',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists chat_messages_thread_created_at_idx
  on public.chat_messages (thread_id, created_at asc);

create table if not exists public.chat_attachments (
  id uuid primary key,
  message_id uuid not null references public.chat_messages(id) on delete cascade,
  user_id text not null,
  filename text not null,
  content_type text not null,
  object_key text not null,
  public_url text,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists chat_attachments_message_created_at_idx
  on public.chat_attachments (message_id, created_at asc);

drop trigger if exists chat_threads_set_updated_at on public.chat_threads;
create trigger chat_threads_set_updated_at
before update on public.chat_threads
for each row
execute function public.set_updated_at();
