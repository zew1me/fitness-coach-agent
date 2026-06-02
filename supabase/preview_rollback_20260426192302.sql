-- ============================================================
-- One-off rollback script for the preview Supabase project.
-- NOT a tracked migration. Reverses the orphan migration
-- `20260426192302` (formerly `0004_rls_and_security.sql` from
-- branch `fix/issue-111-112-db-security`, PR #122, commit e38d1b6)
-- that was pushed directly to preview on 2026-04-26 but never
-- merged to main.
--
-- After running this against preview, follow up with:
--   supabase migration repair --status reverted 20260426192302
--   supabase db push   # applies 0004_chat_messages_parts.sql
--
-- The RLS work itself is preserved in git history at commit
-- e38d1b6 and will be re-introduced as `0005_rls_and_security.sql`
-- on a future PR that targets both preview and prod.
-- ============================================================

begin;

-- ------------------------------------------------------------
-- Drop policies + disable RLS on each table the migration touched
-- (idempotent: `if exists` on policy drops, RLS disable is a no-op
-- when already disabled)
-- ------------------------------------------------------------

drop policy if exists "Users can manage their own profile" on public.athlete_profiles;
alter table public.athlete_profiles disable row level security;

drop policy if exists "Users can manage their own thresholds" on public.sport_thresholds;
alter table public.sport_thresholds disable row level security;

drop policy if exists "Users can manage their own goals" on public.goals;
alter table public.goals disable row level security;

drop policy if exists "Users can manage their own training plans" on public.training_plans;
alter table public.training_plans disable row level security;

drop policy if exists "Users can manage their own plan workouts" on public.plan_workouts;
alter table public.plan_workouts disable row level security;

drop policy if exists "Users can manage their own activities" on public.activities;
alter table public.activities disable row level security;

drop policy if exists "Users can manage their own load snapshots" on public.daily_load_snapshots;
alter table public.daily_load_snapshots disable row level security;

drop policy if exists "Users can manage their own recovery logs" on public.recovery_logs;
alter table public.recovery_logs disable row level security;

drop policy if exists "Users can manage their own schedule" on public.schedule_availability;
alter table public.schedule_availability disable row level security;

drop policy if exists "Users can manage their own schedule overrides" on public.schedule_overrides;
alter table public.schedule_overrides disable row level security;

drop policy if exists "Users can view their own OAuth grants" on public.oauth_grants;
alter table public.oauth_grants disable row level security;

drop policy if exists "Users can view their own authorization codes" on public.oauth_authorization_codes;
alter table public.oauth_authorization_codes disable row level security;

drop policy if exists "Users can view their own refresh tokens" on public.oauth_refresh_tokens;
alter table public.oauth_refresh_tokens disable row level security;

drop policy if exists "Users can manage their own chat threads" on public.chat_threads;
alter table public.chat_threads disable row level security;

drop policy if exists "Users can manage their own chat messages" on public.chat_messages;
alter table public.chat_messages disable row level security;

drop policy if exists "Users can manage their own chat attachments" on public.chat_attachments;
alter table public.chat_attachments disable row level security;

-- ------------------------------------------------------------
-- Restore public.set_updated_at() to its pre-#111 definition
-- (drops `security invoker` qualifier and the `set search_path = ''`
-- that #111 added). Body matches supabase/migrations/0001_schema.sql.
-- ------------------------------------------------------------

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

commit;
