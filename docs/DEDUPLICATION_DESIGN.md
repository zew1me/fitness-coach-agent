# Activity Deduplication Design

This document explains how the fitness-coach app will present one athlete-facing
record per workout when the same session has been ingested several times, without
deleting any of the rows it actually received.

**Status: proposed.** Nothing described here is implemented yet. Every reference
to existing code is real and current; every reference to new code is a plan.

## Motivation

From issue #397, the coach's own words to the athlete:

> I can identify likely duplicates, but I don't have a delete/merge or
> FIT-deduplication tool, so I can't safely consolidate the records myself.

It then listed four groups — a June 1 ride uploaded multiple times, a June 6 run
that arrived as both a ZIP and a FIT, a June 7 MTB ride, and a June 10 ride with
ZIP-plus-FIT retries — and recommended the athlete mentally exclude the extras
from their own load calculations. That is the wrong division of labour. The
athlete does not care about our data model; they care that they rode once.

Duplicates are not cosmetic. They corrupt two athlete-facing numbers today:

- **Training load double-counts.** `recompute_load_endpoint` sums `a.tss` per
  `activity_date` (`api/index.py:1457-1459`) and feeds the result through
  `recompute_load_series` into `daily_load_snapshots`. Because CTL is an
  exponentially weighted average with `CTL_DAYS = 42`
  (`backend/engine/training_load.py`), one duplicated ride keeps inflating
  fitness for weeks — the error outlives the rows that caused it.
- **Compliance invents a workout the athlete never did.**
  `match_activities_to_workouts` (`backend/services/compliance.py:85-120`) is a
  strict 1:1 assignment, so only one copy binds to the planned workout. The other
  keeps `planned_workout_id is None` and is swept into the unplanned list
  (`backend/services/compliance.py:229`). The coach then congratulates the athlete
  on an extra session.

---

## Terminology

Issue #397 and its plan comment use the word **canonical**. This document
deliberately does not, because that single word was covering two independent
questions and made them look like a trade-off:

| Question                             | Answer                                                                          |
| ------------------------------------ | ------------------------------------------------------------------------------- |
| Which row's `id` survives?           | The **first-created** row in the group, permanently                             |
| Where do its field values come from? | A field-level merge across **all** members of the group, richest source winning |

Because these are independent, the surviving row keeps a stable `id` _and_
carries the FIT's richer numbers. There is no version of this where you must pick
one. The surviving row is called the **merged record**; the others are **merged
away**.

---

## What the current tree already does

There is exactly one deduplication mechanism today, and it covers one source.

| Ingestion path            | `source`                                       | `source_file_key`                   | Dedup today                                                                        |
| ------------------------- | ---------------------------------------------- | ----------------------------------- | ---------------------------------------------------------------------------------- |
| FIT/GPX/TCX single upload | `fit_upload` / `gpx_upload` / `tcx_upload`     | R2 key containing a fresh `uuid4()` | **none**                                                                           |
| ZIP member                | same as above                                  | the **archive's** key, shared       | **none**, non-unique by design                                                     |
| Text extract              | `text_extract`                                 | `NULL`                              | **none**                                                                           |
| Screenshot                | _(never persisted under `screenshot_extract`)_ | —                                   | n/a — becomes `text_extract`                                                       |
| Intervals.icu sync        | `intervals_sync`                               | `intervals:{id}`                    | in-process key set + generated-column unique constraint + `ON CONFLICT DO NOTHING` |
| Garmin sidecar            | _(re-enters as an upload)_                     | R2 key                              | filesystem `{activityId}.fit` skip only                                            |

Two facts from that table drive the whole design.

**Exact-key dedup cannot work for uploads.** `R2Service.build_object_key`
(`backend/services/r2.py:134-144`) builds every key from a fresh `uuid4()`:

```python
object_name = f"{uuid4()}{extension}"
return str(PurePosixPath("users", user_id, purpose_segment, date_prefix, object_name))
```

Re-uploading a byte-identical FIT therefore produces a different
`source_file_key`. A uniqueness constraint is structurally incapable of catching
the most common duplicate. Field-based heuristics are not a preference here; they
are the only option.

**The Intervals constraint is narrower than it looks, and stays untouched.** It is
not `source_file_key` uniqueness. It is a _generated_ column that is non-null for
exactly one source (`supabase/migrations/20260716000000_intervals_sync_idempotency.sql`):

```sql
-- Keep Intervals imports idempotent without constraining other import sources:
-- a ZIP can legitimately create several activities with the same source_file_key.
alter table public.activities
  add column intervals_source_file_key text generated always as (
    case when source = 'intervals_sync' then source_file_key end
  ) stored;

alter table public.activities
  add constraint activities_intervals_source_file_key_unique
  unique (user_id, intervals_source_file_key);
```

Postgres treats NULLs as distinct in unique constraints, so every non-Intervals
row is completely unconstrained — deliberately, as the migration's own comment
says. **This path does not change.** `create_intervals_activity`
(`backend/repos/supabase_repo.py:444-460`) keeps its
`on_conflict="user_id,intervals_source_file_key", ignore_duplicates=True` upsert,
and the in-process pre-filter in `intervals_sync` (`api/index.py:584-616`) keeps
its key set. What this design adds is the cross-source case that constraint was
never meant to cover: an Intervals row and a FIT upload of the same ride live in
disjoint key namespaces and can only be matched on their fields.

**Nothing can currently be removed.** `backend/repos/supabase_repo.py` has
`create_activity`, `create_intervals_activity`, `get_activity`, `update_activity`,
`list_activities`, `list_activities_between`, and `list_synced_intervals_keys` —
no `delete_activity`, no merge. "No rows are hard-deleted" is a description of the
current tree, not only a policy this document adopts.

---

## Database schema — two new columns on `activities`

| Column                    | Type                                                             | Purpose                                          |
| ------------------------- | ---------------------------------------------------------------- | ------------------------------------------------ |
| `dedup_status`            | `text not null default 'active'`, check `in ('active','merged')` | The single predicate every read path filters on  |
| `merged_into_activity_id` | `uuid references public.activities(id) on delete cascade`        | Points a merged-away row at its surviving record |

Plus a supporting index on `(user_id, merged_into_activity_id)` and a check that
any `merged` row carries a non-null `merged_into_activity_id`.

**The self-FK is `on delete cascade`, not `on delete set null`.** The two cannot
coexist: `set null` would blank a child's `merged_into_activity_id` while the row
still reads `dedup_status = 'merged'`, firing the check constraint in the middle
of the delete. Postgres cannot defer CHECK constraints, so the delete simply
errors. No application path deletes activities today, but
`activities.user_id references athlete_profiles(user_id) on delete cascade`
(`supabase/migrations/0001_schema.sql:231`) means account deletion reaches this.
Cascade is also the semantically correct pairing — a merged-away row without its
survivor describes nothing.

Note that `public.activities` has **RLS disabled and zero policies**; only
`intervals_connections` and `agent_emails` carry policies. The protected FastAPI
handler derives the athlete id from `require_user_context()`; `p_user_id` is never
accepted from a tool or request body. The repository then calls PostgREST with the
service-role client, so `auth.uid()` inside the RPC would identify neither the
athlete nor the app's custom bearer-token subject. The authenticated handler's id
is the identity propagated to SQL.

The RPC still enforces that identity as a database invariant: it is executable
only by `service_role`, and every row lock and update includes
`user_id = p_user_id`. An id belonging to another athlete is therefore
indistinguishable from a missing id. Merge also rejects a self-merge, a survivor
that is not active, a target already merged into a different survivor, and any
state that would create a chain or cycle. Repeating the exact completed merge is
the only idempotent exception. The unmerge RPC derives the survivor from the
locked merged row and applies the same user predicate to it. Cross-user, self,
invalid-target, chain, and cycle cases are negative database tests, rather than
assumptions left to application callers.

### Read-filter scope, precisely

`dedup_status = 'active'` is applied to **`list_activities` and
`list_activities_between` only**. It is deliberately _not_ applied to:

- **`get_activity`** — unmerge and any merge-audit view must be able to fetch a
  merged-away row by id.
- **`list_synced_intervals_keys`** — a merged-away Intervals row must still block
  re-syncing the same `intervals:{id}`, or the next sync recreates the duplicate.

Because those two list methods are the funnel for the calendar, compliance,
training load, recalibration, and the coach's `get_recent_activities` tool, one
predicate collapses every view at once.

---

## Detection

The scorer lives in `backend/services/activity_dedup.py` and is **pure and
side-effect-free**, mirroring `match_activities_to_workouts`
(`backend/services/compliance.py:85-120`). It takes a set of the athlete's
activities and returns proposed groups; it persists nothing.

It is tiered, because the confidence genuinely differs and auto-merging an
ambiguous pair silently deletes a real workout from the athlete's load.

| Tier  | Condition                                                                                                                                                    | Action                                   |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------- |
| **A** | Casefolded sport equality; both `started_at` present and within **±10 min**; duration within **±5%**; distance within **±5%** when both are present          | Auto-merge at ingestion                  |
| **B** | Same sport; `activity_date` within **±1 day** (timezone slop); duration **or** distance within **±10%**, evaluated over whichever of the two both rows carry | Propose to the athlete, never auto-merge |
| —     | Anything else                                                                                                                                                | Not a group                              |

Sport equality is exact and casefolded, matching `_pair_score`'s existing rule
(`backend/services/compliance.py:53`). The ±1-day window matches
`MATCH_MAX_DAY_OFFSET = 1` (`backend/services/compliance.py:24`).

**Null degradation is the point of Tier B, not an afterthought.** The cases
actually reported in #397 are ZIP-plus-FIT pairs, and the ZIP side frequently has
no `started_at` and no distance at all. A scorer that requires both sides to carry
a start time would miss every duplicate the ticket was filed about. So: a missing
`started_at` on either side drops the pair from Tier A to Tier B rather than
disqualifying it, and a field that is null on one side is skipped rather than
scored as a mismatch. Tier B still requires an actual metric agreement — a pair
with no comparable duration **and** no comparable distance is not a group at all,
since same-sport-same-day on its own describes two genuinely different workouts
just as well as it describes a duplicate.

### Hard negative signals

Two rows are **never** grouped, at any tier, when either holds:

- **They share a ZIP archive's `source_file_key`.** Every member of one archive
  shares the archive's key (`api/index.py:1787-1789`), so this reliably means
  "two distinct workouts the athlete uploaded together."
- **They are already linked to different plan workouts.** The athlete or the coach
  has asserted these are separate sessions.

These guard against the realistic false positives: brick workouts, a race plus its
warmup, and back-to-back interval sets.

**Known limitation, accepted deliberately.** The archive rule also blocks the case
where a single ZIP contains the same ride as both `.fit` and `.gpx`. That pair
will never auto-merge and will never even be proposed. It is the right trade —
the rule is correct for every case in #397 (a ZIP and a separately-uploaded FIT
get different R2 keys, so they are unaffected), and weakening it to catch the
same-archive case would reopen the brick-workout false positive, which is the
failure mode that actually costs the athlete a workout.

---

## The merge

The merge is **materialized**: merged values are written into the surviving row's
columns at merge time. The alternative — keeping every row pristine and folding
groups on each read — was rejected. It would require the calendar, compliance,
training load, recalibration, and `get_recent_activities` all to route through a
resolver, where materializing costs one added predicate and nothing else. It also
matches the house pattern: `merge_activity_text_update`
(`backend/services/activity_text.py:890`) already mutates an activity in place and
records what it did in `raw_extraction`.

The honest cost: the surviving row becomes a hybrid rather than a faithful copy of
any one file. That is exactly why the pre-merge snapshot and unmerge below exist.

### Field-level rules

| Field group                                                                                                                                                                                                                 | Rule                                                                                                                                                                                     |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Device metrics** — `duration_seconds`, `distance_meters`, `elevation_gain_meters`, `avg_hr_bpm`, `max_hr_bpm`, `avg_power_watts`, `normalized_power_watts`, `avg_pace_sec_per_km`, `avg_cadence_rpm`, `zone_distribution` | Take the value from the highest-fidelity member that has one                                                                                                                             |
| **Athlete-authored** — `rpe`, `athlete_notes`, `fatigue_notes`, `fueling_notes`                                                                                                                                             | A device file **never** overwrites these. Prefer any non-null athlete value; when two members disagree, keep the surviving row's and record the conflict in the audit entry              |
| **Derived** — `tss`, `intensity_factor`                                                                                                                                                                                     | Recomputed from the merged inputs, never copied. Mirrors `_clear_derived_load_metrics` (`backend/services/activity_text.py:879`) followed by re-derivation                               |
| `started_at`, `activity_date`                                                                                                                                                                                               | The highest-fidelity member's — a FIT timestamp is authoritative, matching the existing `_date_edit_verdict` / `refused_authoritative` concept (`backend/services/activity_text.py:698`) |
| `activity_summary` jsonb                                                                                                                                                                                                    | Deep-merged, mirroring `_merge_context_summary` (`backend/services/activity_text.py:488`)                                                                                                |
| `source`, `source_file_key`                                                                                                                                                                                                 | The surviving row's are left alone; every member's source and key are recorded in the audit entry so provenance is not lost                                                              |
| **Everything else** — `raw_extraction`, `summary_schema_version`, `planned_workout_id`, `user_id`, `id`, timestamps                                                                                                         | The surviving row's value is kept unchanged. This row is the catch-all: any column not named above is never taken from a merged-away member                                              |

Source fidelity rank, highest first:

```text
fit_upload  >  tcx_upload  >  gpx_upload  >  intervals_sync  >  text_extract / screenshot_extract  >  manual
```

Worked example — the June 6 run from #397, a ZIP member merged with a FIT upload:

```text
before                                     after
──────────────────────────────────────     ──────────────────────────────────────
A  text_extract   id=A                     A  dedup_status=active     id=A
   rpe                7                       rpe                7    (athlete's, kept)
   avg_hr_bpm         null                     avg_hr_bpm       152    (from B)
   avg_cadence_rpm    null                     avg_cadence_rpm   87    (from B)
   tss                61                       tss           <recomputed>

B  fit_upload     id=B                     B  dedup_status=merged     id=B
   rpe                null                     merged_into_activity_id=A
   avg_hr_bpm         152                      (all raw values untouched)
   avg_cadence_rpm    87
```

### Audit trail and unmerge

Each merge appends an entry to the surviving row's
`raw_extraction.merged_from` — following the existing
`raw_extraction["text_updates"]` convention (`backend/services/activity_text.py:904-912`)
— carrying the merged-away row's id, `source`, `source_file_key`, the detection
tier that produced the group, any field conflicts, and a `pre_merge` snapshot of
every surviving-row value the merge overwrote.

**Unmerge is therefore first-class, and this is the strongest argument for the
whole approach.** Because no row is ever deleted, reversing a merge is: clear the
merged-away row's `dedup_status` and `merged_into_activity_id`, drop its audit
entry, and recompute the surviving row's fields. A false-positive merge costs the
athlete one conversation, not a workout.

**Unmerge re-derives; it does not restore from the snapshot.** This matters as
soon as a group has three members, because each `pre_merge` snapshot is a delta
against a base that the next merge moves:

```text
A survives, avg_hr_bpm = null
  merge B (fit, hr 150)   →  A.hr = 150,  pre_merge#1 = {hr: null}
  merge C (fit, hr 152)   →  A.hr = 152,  pre_merge#2 = {hr: 150}

unmerge C  → snapshot restore gives hr = 150 ✓
unmerge B  → snapshot restore gives hr = null ✗  — but C is still merged in
                                                   and supplied 152
```

Restoring out of order silently discards a still-merged member's contribution. So
unmerge instead re-runs the same pure field-merge over whichever members remain
active, which is order-independent and reuses the function
`backend/services/activity_dedup.py` already owns. `pre_merge` stays in the audit
entry as a debugging and provenance record, not as the restore mechanism.

This is also why `unmerge_activity` takes pre-computed fields exactly as
`merge_activity` does — the RPC stays responsible for atomicity, never for merge
policy.

### The plan-workout link is re-pointed at write time

Merging must unwind a link that has no FK-level protection.
`activities.planned_workout_id` and `plan_workouts.actual_activity_id`
(`supabase/migrations/0001_schema.sql:274-275`) are kept mutually consistent only
by three service-role RPCs — and `unlink_plan_workout_from_activity` **raises
`22023` when the two sides do not agree**
(`supabase/migrations/20260806003910_unlink_plan_workout_from_activity_atomic.sql:51-58`).

So the merge RPC re-points `plan_workouts.actual_activity_id` to the surviving row
inside the same transaction, rather than having reads resolve through
`merged_into_activity_id`. Read-time resolution would leave that assertion pointed
at a merged-away row and start failing the unlink RPC. There is precedent for
read-time mirrors of a write-time invariant in this codebase —
`_scope_planned_workouts_to_active_plan` (`api/index.py`, issue #315) — but here
the write side must own it, because another RPC already asserts on the stored
state.

### Ordering at ingestion: detect before matching

`_try_match_activity_to_plan` runs on every activity create
(`api/index.py:2455`, inside `_finalize_persisted_activity`), and
`match_plan_workout_to_activity` clears the _prior_ activity's reverse link when a
workout is reassigned
(`supabase/migrations/20260703110000_plan_workout_atomic_rpc.sql:65-70`).

If a newly-created duplicate is matched to workout W first, it steals W from the
surviving row and blanks the survivor's `planned_workout_id` — which the merge
then has to hand back. Running Tier-A detection **before** plan-matching means the
duplicate is already merged away and never competes for the workout. This ordering
is load-bearing, not incidental.

### Merge RPCs

Both follow the template in
`supabase/migrations/20260703110000_plan_workout_atomic_rpc.sql`: `security
definer`, `set search_path = public`, explicit `p_user_id`, `select … for update`
in a fixed lock order, `errcode 'P0002'` for not-found and `'22023'` for an
invalid request, and the grants block verbatim:

```sql
revoke all on function public.merge_activity(text, uuid, uuid, jsonb)
  from public, anon, authenticated;
grant execute on function public.merge_activity(text, uuid, uuid, jsonb)
  to service_role;
```

| RPC                                                                       | Behaviour                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `merge_activity(p_user_id, p_surviving_id, p_merged_id, p_merged_fields)` | Locks both rows in id order; sets the merged row's `dedup_status`/`merged_into_activity_id`; applies the pre-computed merged field values and audit entry to the surviving row; re-points `plan_workouts.actual_activity_id` if the merged row held the link. Refuses to merge a row into itself (`22023`). Idempotent: re-merging an already-merged row returns current state rather than erroring, following the early-return in `unlink_plan_workout_from_activity` |
| `unmerge_activity(p_user_id, p_merged_id, p_rederived_fields)`            | Clears the merged row's `dedup_status`/`merged_into_activity_id`, drops its audit entry, and writes the fields re-derived from the remaining active members. Idempotent on an already-active row                                                                                                                                                                                                                                                                       |

Field merging itself happens in Python (`backend/services/activity_dedup.py`), not
in SQL — the rules involve source-fidelity ranking, jsonb deep-merge, and load
re-derivation that already exist as tested Python helpers. The RPC's job is
atomicity, not policy.

---

## Training load has to be rebuilt, and the current endpoint cannot do it

Merging fixes the rows, but `daily_load_snapshots` keeps the inflation. Worse, the
existing endpoint cannot currently rebuild it. `recompute_load_endpoint`
(`api/index.py:1439-1484`) seeds from the **latest** snapshot and walks forward:

```python
prev = await repo.get_latest_load(user_id, sport=payload.sport)
initial_ctl = prev.ctl if prev else 0.0
initial_atl = prev.atl if prev else 0.0
snapshots = recompute_load_series(daily_tss, since, date.today(), initial_ctl, initial_atl)
```

`get_latest_load` orders by `snapshot_date desc limit 1`
(`backend/repos/supabase_repo.py:574-584`), so the seed is the already-inflated
value. Pushing `since` further back does not help — the seed still comes from the
newest row, not from the day before `since`. Two changes:

1. **Seed at a date.** Add `get_load_snapshot_on_or_before(user_id, sport, on_date)`
   and seed `initial_ctl`/`initial_atl` from the day _before_ `since`, so a window
   rebuild is actually correct.
2. **Recompute over `[affected activity_date, today]`** on merge and unmerge.

**Bounded by a horizon.** Skip the recompute when the affected date is older than
`_LOAD_RECOMPUTE_HORIZON_DAYS = 90` (≈ 2 × `CTL_DAYS`). A single session's residual
contribution to today's CTL decays as `(41/42)^N`, since
`ctl += (tss - ctl) / 42` (`backend/engine/training_load.py`):

| Age of the duplicate | Residual share | CTL error from a duplicated 100-TSS session                                   |
| -------------------- | -------------- | ----------------------------------------------------------------------------- |
| 0 days               | 100%           | ≈ 2.4                                                                         |
| 42 days              | ≈ 36%          | ≈ 0.9 — material                                                              |
| 90 days              | ≈ 11%          | ≈ 0.27 — below noticeable, and below the snapshot's 0.1 rounding meaningfully |

Beyond 90 days the correction costs a full window rebuild to move a number the
athlete cannot see; the calendar's past window is 42 days anyway. When the
recompute is skipped, say so in the merge result and the audit entry rather than
staying silent about it.

---

## End-to-end flow

```text
INGESTION (new activity arrives — upload, ZIP member, text, or Intervals sync)
 1. Persist the row exactly as received                → repo.create_activity
 2. Load the athlete's recent activities               → repo.list_activities (active only)
 3. Score the new row against them                     → activity_dedup scorer (pure)
      ├─ Tier A  → merge_activity RPC, then continue at step 5
      ├─ Tier B  → record nothing; surfaced later by find_duplicate_activities
      └─ no match → continue at step 4
 4. Plan-match the new row                             → _try_match_activity_to_plan
      (skipped when step 3 merged it away — detection runs BEFORE matching so the
       duplicate never steals the planned workout from the surviving row)
 5. Recompute load over [activity_date, today]         → if within the 90-day horizon

BACKLOG CLEANUP (the athlete asks, or the coach suspects)
 1. Coach calls find_duplicate_activities              → returns Tier A + Tier B groups
 2. Coach presents the groups to the athlete           → in the athlete's own terms
 3. Athlete confirms                                   → REQUIRED for Tier B
 4. Coach calls merge_duplicate_activities             → merge_activity RPC per pair
 5. Recompute load                                     → horizon-bounded, as above

READS (all of them, automatically)
   ... where dedup_status = 'active'          ← the only change to every consumer

REVERSAL
   unmerge_activity → restores from raw_extraction.merged_from[].pre_merge
```

---

## Per-layer breakdown

### Migration

One timestamped migration adding the two columns, the index, and the check
constraint; a companion (or the same file) defining `merge_activity` and
`unmerge_activity`. `docs/supabase-migration-history.md` gets an entry in the same
change — Change / Why / Security note — and the file is appended to the canonical
migration sequence, per the repo's Database convention.

### Repo — `backend/repos/supabase_repo.py`

- `list_activities` and `list_activities_between` gain
  `.eq("dedup_status", "active")`.
- `get_activity` and `list_synced_intervals_keys` are unchanged (see
  "Read-filter scope" above).
- New RPC wrappers for `merge_activity` and `unmerge_activity`, following the
  `match_plan_workout_to_activity` wrapper shape
  (`backend/repos/supabase_repo.py:893-911`).
- New `get_load_snapshot_on_or_before` for the load seeding fix.

### Services — `backend/services/activity_dedup.py` (new)

Pure scorer plus the field-merge reconciler. No I/O, no persistence — the same
shape as `backend/services/compliance.py`, and testable without a database.
Conflicts it refuses to resolve are returned in the structured
`{field, value, reason}` shape already used by `RejectedActivityUpdate`
(surfaced as `response["rejected_updates"]`, `api/index.py:2377`).

### Engine endpoints — `api/index.py`

- Tier-A auto-merge wired into `_finalize_persisted_activity`, **before** the
  existing `_try_match_activity_to_plan` call at `api/index.py:2455`.
- `POST /api/engine/find-duplicate-activities` and
  `POST /api/engine/merge-duplicate-activities`, plus an unmerge endpoint.
- `recompute_load_endpoint` switched to the seed-at-date lookup.

### Agent — `lib/agent/`

- `find_duplicate_activities`, `merge_duplicate_activities`, and
  `unmerge_activity` defined in `lib/agent/tools.ts` via `defineTool` and
  registered in `coachToolDefinitions`. Note the schema rule at
  `lib/agent/tools.ts:10-13`: nested object fields must be `.nullable()`, not
  `.optional()`. Activity ids are `z.uuid()` so the model cannot resolve against a
  fabricated id, matching `resolvePlanWorkoutInputSchema`
  (`lib/agent/tools.ts:259-266`).
- Routed through the existing `postEngine` table in
  `lib/agent/coach-tools.ts:290-320`.
- `merge_duplicate_activities` takes a surviving id and a list of ids to merge
  away, so the coach can only act after the athlete has confirmed.
- `lib/agent/system-prompt.ts` (`buildLeadCoachPrompt`) gains guidance replacing
  today's "I don't have a delete/merge tool" behaviour: call
  `find_duplicate_activities` when the athlete asks or duplicates are suspected,
  present the groups plainly, and **confirm before merging anything from Tier B**.

### Frontend — `components/coach-calendar.tsx`

Canonical-only filtering happens in the backend, so duplicates simply stop
rendering. When a record consolidates others, show a small "merged from N" badge
reusing the existing `.moreCount` styling
(`components/coach-calendar.module.css:335-338`) rather than a new UI primitive.
`calendarActivitySchema` is a `z.looseObject` (`lib/schemas.ts:176-189`), so the
backend can attach the count without a schema change, though adding it explicitly
is clearer.

Two existing behaviours the implementation must not break: chips are assembled
planned-first so recorded activities are what the `+N more` cutoff hides
(`components/coach-calendar.tsx:430`), and the narrow-viewport media query
collapses chips to 16px dots (`components/coach-calendar.module.css:558-608`,
`.chipLabel { display: none }` at `:596`), so a badge must survive dot mode.

---

## What this explicitly does not change

- **No rows are hard-deleted, ever.** There is no `delete_activity` in the repo
  today and this design does not add one. Every raw row the athlete sent us stays
  exactly as received; only `dedup_status` and `merged_into_activity_id` are
  written on it.
- **Intervals-sync dedup is untouched.** The generated `intervals_source_file_key`
  column, its unique constraint, the `ignore_duplicates=True` upsert in
  `create_intervals_activity`, and the in-process key set in `intervals_sync` all
  behave exactly as they do now. This design sits alongside it and handles the
  cross-source case that constraint was never designed to cover.
- **Compliance matching logic is unchanged.** `match_activities_to_workouts` keeps
  its existing scoring; it simply stops being handed duplicate rows.

---

## Tests

**Planned coverage — none of these exist yet.** Listed here so the implementation
phases inherit a concrete target.

| File                                               | What it should cover                                                                                                                                                                                                                                                  |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/python/test_activity_dedup.py`              | Pure scorer: Tier A/B boundaries at each threshold, null `started_at` degradation, null distance skipped rather than penalised, both hard negative signals, group formation with three or more members                                                                |
| `tests/python/test_activity_dedup.py` (merge half) | Field rules: device-metric fidelity ranking, athlete-authored fields never overwritten by a device file, conflict recorded, `tss` recomputed not copied, `activity_summary` deep-merge, unlisted columns untouched, `pre_merge` snapshot completeness                 |
| `tests/python/test_activity_dedup.py` (unmerge)    | Re-derivation is order-independent: in a three-member group, unmerging the **middle** member preserves the remaining member's contribution — the case a `pre_merge` snapshot restore would corrupt                                                                    |
| `tests/python/test_supabase_repo.py`               | `merge_activity` / `unmerge_activity` RPC names and `p_*` argument dicts, mirroring `test_match_plan_workout_to_activity_uses_atomic_rpc`; `dedup_status` filtering present on the two list methods and **absent** on `get_activity` and `list_synced_intervals_keys` |
| `tests/python/test_supabase_db.py`                 | RPC invariants reject cross-user ids, self-merges, invalid or already-merged targets, chains, and cycles; an exact retry remains idempotent                                                                                                                           |
| `tests/python/test_api.py`                         | Detection runs before plan-matching in `_finalize_persisted_activity`; a Tier-A duplicate never acquires the planned workout                                                                                                                                          |
| `tests/python/test_engine.py`                      | `get_load_snapshot_on_or_before` seeding rebuilds a window correctly; horizon skip beyond 90 days                                                                                                                                                                     |
| `tests/python/test_compliance_api.py`              | A merged-away row no longer appears as an unplanned session                                                                                                                                                                                                           |
| `tests/python/test_calendar_api.py`                | Calendar returns only `dedup_status = 'active'` rows                                                                                                                                                                                                                  |
| `tests/python/test_intervals_sync.py`              | Intervals dedup behaviour is unchanged, and a merged-away Intervals row still blocks re-sync                                                                                                                                                                          |
| `tests/web/agent-tools.test.ts`                    | The three new tools' request shapes and the `coachToolDefinitions` surface snapshot                                                                                                                                                                                   |
| `tests/ui/calendar.spec.ts`                        | The merged-from badge, including narrow-viewport dot mode                                                                                                                                                                                                             |

`@pytest.mark.db` tests are excluded from the default run
(`addopts = "-m 'not db and not oai'"`); run them with `bun run test:db` against a
local or preview Supabase project.
