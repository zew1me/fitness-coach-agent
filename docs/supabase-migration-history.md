# Supabase Migration History

## Canonical migration sequence

- `0001_schema.sql` — initial application schema
- `0002_nutrition.sql` — nutrition tracking
- `0003_fitness_thresholds.sql` — threshold provenance
- `0004_chat_messages_parts.sql` — AI SDK message-parts persistence
- `20260624055541_specialization_pct_nullable.sql` — nullable sport specialization for multi-sport athletes
- `20260625172251_chat_model_state.sql` — private, versioned Agents SDK replay state and turn leases
- `20260626000000_activity_summary.sql` — rich activity summary persistence
- `20260702000000_agent_emails.sql` — inbound agent email storage for autonomous preview testing
- `20260703090000_remove_pending_tool_outputs_from_model_state.sql` — one-time cleanup of alpha placeholder tool outputs from durable replay state

`20260625172251` deliberately stores compactable model context separately from
`chat_messages`. Applying or resetting model state must never rewrite the
athlete-visible transcript.

Supabase checks can fail with:

```text
Remote migration versions not found in local migrations directory.
```

when `supabase_migrations.schema_migrations` contains a version that does not
exist under `supabase/migrations/`.

## Preview repair from June 2026

The standalone Supabase Preview project had a remote-only version:

```text
20260426192302
```

That version came from a security migration created on 2026-04-26 that enabled
RLS and fixed the `set_updated_at` function search path. It was introduced on
the never-merged branch behind
[PR #122](https://github.com/zew1me/fitness-coach-agent/pull/122) (commit
[`e38d1b677ef73b2e06ea13d8169e3d24a7b50e7e`](https://github.com/zew1me/fitness-coach-agent/commit/e38d1b677ef73b2e06ea13d8169e3d24a7b50e7e),
file `supabase/migrations/0004_rls_and_security.sql`) and pushed directly to
preview from that branch — so `main` never contained a matching local migration
file, and `0004` later got reused by `0004_chat_messages_parts.sql`.

The repair path was operational, not a new committed migration:

- execute a one-off SQL script against the preview project that drops the 16
  RLS policies the orphan migration created, disables RLS on those tables, and
  restores `public.set_updated_at()` to its pre-#111 definition (matching the
  body in `supabase/migrations/0001_schema.sql`)
- mark the orphan version reverted with
  `supabase migration repair --status reverted 20260426192302`
- let `supabase db push` apply the real `0004_chat_messages_parts.sql` migration

The rollback script lived in-tree transiently as
`supabase/preview_rollback_20260426192302.sql` while it was applied to preview
and was then removed. If a similar repair is ever needed on another
environment, recover the script body from `main`'s history at commit
[`354a239643d72340c5df63cf869d52f68ee163ec`](https://github.com/zew1me/fitness-coach-agent/commit/354a239643d72340c5df63cf869d52f68ee163ec):

```bash
git show 354a239643d72340c5df63cf869d52f68ee163ec:supabase/preview_rollback_20260426192302.sql
```

Do not restore `20260426192302` as a local migration unless the RLS work from
PR #122 is being reintroduced intentionally as a new forward migration for
every environment.

Supabase Git preview branches have their own project refs. A branch can continue
to report stale remote-only versions even after the standalone preview project is
repaired. Check the Supabase PR comment or run:

```bash
supabase branches list --project-ref <parent-preview-project-ref> --experimental
```

If a Git preview branch contains stale history from an earlier failed migration
attempt, reset/recreate that branch or repair its branch database history. Do not
rename `0004_chat_messages_parts.sql` to match a stale branch-only remote
version; the canonical local migration sequence is `0001`, `0002`, `0003`,
`0004`.

## Verification

Use `migration list` to confirm local and remote history are aligned:

```bash
supabase link --project-ref <project-ref>
supabase migration list --linked --password <database-password>
```

Then dry-run the push:

```bash
supabase db push --linked --dry-run --password <database-password>
```

After this repair, the preview project should have no remote-only versions. If
`0004_chat_messages_parts.sql` is already applied, `db push --dry-run` should
report no pending migrations. Otherwise, the only expected pending migration is:

```text
0004_chat_messages_parts.sql
```

## If a future remote-only version appears

First identify whether the remote-only version is a real schema change or an
accidental history row.

- If it is a real schema change, restore it as a local migration file with the
  exact remote version.
- If it is only an accidental history row and the schema was never applied,
  repair the remote history with `supabase migration repair --status reverted`.

Do not add empty dummy migrations. Empty files hide drift instead of preserving
the schema change that actually reached the database.

## 20260624055541 — specialization_pct nullable (2026-06-24)

**File:** `supabase/migrations/20260624055541_specialization_pct_nullable.sql`

**Change:** `ALTER TABLE athlete_profiles ALTER COLUMN specialization_pct DROP NOT NULL, ALTER COLUMN specialization_pct DROP DEFAULT`

**Why:** The multi-sport redesign (issue #254) allows athletes to have no single-sport
specialization (duathletes, triathletes, etc.). The old `NOT NULL DEFAULT 80` caused
a constraint violation when the AI omitted the field for multi-sport athletes and the
column default was missing on a drifted preview DB. `NULL` is now the correct sentinel
for "unspecialized"; the 0–100 check constraint is preserved.

**Version note:** This change was first applied directly to the preview DB on
2026-06-24, which recorded it under the timestamp version `20260624055541`. The
migration file therefore uses that exact version (not a `0005_` sequence number)
so local/production history converges on the already-applied remote version
instead of reporting drift.

**All environments:** Apply via `supabase db push` (or `bun run db:reset` locally).
Preview already has this version recorded, so it is a no-op there.

## 20260625172251 — chat model state (2026-06-25)

**File:** `supabase/migrations/20260625172251_chat_model_state.sql`

**Change:** creates `public.chat_model_states` (durable, versioned Agents SDK
replay state and turn leases). See the table definition in the migration file.

**Version note:** This migration was first committed as `0005_chat_model_state.sql`
([commit `514705c`](https://github.com/zew1me/fitness-coach-agent/commit/514705c))
and then renamed to the timestamp version `20260625172251`
([commit `8410b5c`](https://github.com/zew1me/fitness-coach-agent/commit/8410b5c))
to converge with the timestamp-based history. Do **not** rename it back to `0005`
to match a stale branch-only remote version — that is the anti-pattern called out
above.

### Preview branch repair (PR #258, 2026-06-26)

The rename left an orphaned `0005` version recorded in the PR #258 Git preview
branch DB (`algomspgrabvcosiwkqq`). Because `20260625172251` had not applied
there, two symptoms appeared together:

- The **Supabase Preview** check failed with
  `Remote migration versions not found in local migrations directory`, and the
  branch sat in `MIGRATIONS_FAILED`.
- The deployed app threw
  `PGRST205: Could not find the table 'public.chat_model_states'`
  (`backend/repos/supabase_repo.py`, `acquire_chat_turn_lease`), because the
  table was never created.

**Remedy:** reset the ephemeral preview branch via the Supabase API
(`reset_branch`, MCP) — **not** a new committed migration and **not** a local
rename. The reset recreates the branch DB and replays the current git migration
files (`0001`–`0004`, `20260624055541`, `20260625172251`, and later timestamp
migrations such as `20260626000000`) in order, which clears the `0005` orphan
and creates `chat_model_states`. For an ephemeral, `with_data:false` Git preview
branch, resetting is preferred over CLI `migration repair`: it replays the
canonical local files instead of hand-editing remote history, and there is no
branch data to lose.

**All environments:** Apply via `supabase db push` (or `bun run db:reset`
locally). Production/parent never recorded the `0005` orphan, so the timestamp
migration applies cleanly there on merge.

## 20260626000000 — activity summary object (2026-06-26)

**File:** `supabase/migrations/20260626000000_activity_summary.sql`

**Change:** Adds `activities.summary_schema_version` and
`activities.activity_summary jsonb`, then refreshes the `activities.source`
check constraint to include `tcx_upload`.

**Why:** Activity ingest now stores a compact, rich summary object at ingest
time so GPX/FIT/text-derived activities can retain coaching-grade aggregates,
estimates, confidence scores, source quality, fueling, subjective notes, and
distribution summaries without retaining raw time-series files indefinitely.

**All environments:** Apply via `supabase db push` (or `bun run db:reset`
locally).

## 20260702000000 — agent email inbox rows (2026-07-02)

**File:** `supabase/migrations/20260702000000_agent_emails.sql`

**Change:** Creates `public.agent_emails` for signed Mailgun inbound email
payloads. Rows store recipient, sender, subject, text and HTML bodies, raw
Mailgun metadata, and `consumed_at` for reader tooling. The migration adds
recipient/time indexes for newest-first lookup and unconsumed inbox queries.

**Why:** Issue #264 needs agents to receive OTP, magic-link, and preview-test
emails autonomously. Storing the small inbound payloads in Supabase keeps the
flow on the existing Vercel/Supabase stack and avoids object storage for
messages that should be short-lived.

**Security note:** Row level security is enabled with no browser-facing
policies. Ingestion and reader tooling must use server-only or local trusted
service role credentials.

**All environments:** Apply via `supabase db push` (or `bun run db:reset`
locally).

## 20260703090000 — remove pending tool outputs from model state (2026-07-03)

**File:** `supabase/migrations/20260703090000_remove_pending_tool_outputs_from_model_state.sql`

**Change:** Removes stale `pending_implementation` tool-call outputs, plus their
matching `function_call` items, from `chat_model_states.items`. The migration
increments the model-state `version` and records
`removed_pending_tool_outputs_at` in `compaction_metadata`.

**Why:** Early alpha durable sessions could contain placeholder outputs from
tools that were advertised before implementation. The Agents SDK rejected one
such replay item on the `empty-bubble-fix` preview deployment with
`Unsupported item {"type":"function_call_output", ... "status":"pending_implementation"}`,
causing `/api/chat` to return the bounded `503` fallback. This is private model
replay state, not the athlete-visible transcript, so the correct alpha repair is
a data cleanup rather than retaining compatibility for obsolete placeholder
history forever.

**Preview before applying:**

```sql
select thread_id, user_id
from public.chat_model_states
where items::text like '%pending_implementation%';
```

**All environments:** Apply via `supabase db push` (or `bun run db:reset`
locally). This migration is a no-op where no stale placeholder tool outputs
exist.
