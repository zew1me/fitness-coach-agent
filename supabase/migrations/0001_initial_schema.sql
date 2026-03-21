create extension if not exists pgcrypto;

create table if not exists public.athlete_profiles (
  user_id text primary key,
  age integer,
  constraints text[] not null default '{}',
  cycling_ftp_watts integer,
  goals text[] not null default '{}',
  injuries_rehab text[] not null default '{}',
  notes text,
  weight_kg double precision,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.check_ins (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.athlete_profiles(user_id) on delete cascade,
  effective_date date,
  image_count integer not null default 0 check (image_count >= 0),
  raw_text text not null,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists check_ins_user_id_created_at_idx
  on public.check_ins (user_id, created_at desc);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

drop trigger if exists athlete_profiles_set_updated_at on public.athlete_profiles;
create trigger athlete_profiles_set_updated_at
before update on public.athlete_profiles
for each row
execute function public.set_updated_at();
