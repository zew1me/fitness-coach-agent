# Supabase Migration History

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

## PR preview branch databases

Supabase Git preview branches create and migrate a separate branch project per
PR. Vercel PR preview deployments must use the matching branch project's
Supabase environment variables. The long-lived shared preview project
`psbteexygkspyotkyflc` is only a fallback for non-PR preview workflows.

`bun run build` runs `scripts/verify-preview-supabase-env.ts` before `next build`
and fails Vercel PR preview builds when Supabase URLs still point at
`psbteexygkspyotkyflc`. Fix this by connecting the Supabase Vercel integration
so it creates branch-scoped Vercel preview variables, then redeploy the PR
preview.
