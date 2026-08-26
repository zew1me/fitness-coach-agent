# Activity Materialization & Deduplication

**Status: proposed, not implemented.** Every reference to existing code is real and
current. Every reference to new tables, columns, RPCs, and services is a plan.

Issue: #397. Prerequisite bug fixes: #461, #462. Related follow-ups: #463, #464.

## Context

Today `activities` rows are shown to the athlete exactly as ingested. One real-world
workout that arrives twice — a FIT upload plus the Intervals.icu sync of the same ride,
a ZIP re-import, a re-uploaded file — becomes two rows presented as two workouts.

That corrupts two athlete-facing numbers:

- **Training load double-counts.** `recompute_load_endpoint` (`api/index.py:1439`) sums
  `tss` per `activity_date`, and CTL is an exponentially-weighted average with
  `CTL_DAYS = 42` (`backend/engine/training_load.py`), so one duplicated ride keeps
  inflating fitness for weeks.
- **Compliance invents a workout.** `match_activities_to_workouts`
  (`backend/services/compliance.py:85`) is a strict 1:1 assignment, so only one copy
  binds to the planned workout. The other keeps `planned_workout_id is None` and is
  reported as an _unplanned_ session (`compliance.py:229`), and the coach congratulates
  the athlete on a session they never did.

There is one dedup mechanism today and it covers one source: `intervals_source_file_key`,
a generated column that is `NULL` for every non-Intervals row
(`20260716000000_intervals_sync_idempotency.sql`). Uploads, ZIP members, and text
extracts have **zero** duplicate protection.

## What this changes

Each ingested input is stored immutably and separately in a new `activity_sources`
table. That table is the record of what arrived. The `activities` row becomes what the
athlete, the calendar, and the coach read.

An exact duplicate from the same provenance is rejected outright; anything else is
stored and merged into one presented activity.

### What `activities` actually becomes

It is **not** a materialized view, and describing it as one sets the wrong expectation.
A view has a single writer and no independent state. This table has four distinct jobs,
and the ownership matrix under "Projection ownership" is the enumeration of them:

| Job                 | Columns                                                                     | Nature                                                                            |
| ------------------- | --------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| **Projection**      | every metric, `sport`, dates, the legacy provenance triplet, derived values | Recomputed from the live source set. This is the part that behaves like a view.   |
| **Identity anchor** | `id`, `user_id`, `created_at`                                               | Stable forever. `plan_workouts.actual_activity_id` and every index depend on it.  |
| **Link state**      | `planned_workout_id`                                                        | An explicit assertion by the athlete or coach. Never derived, never field-merged. |
| **Lifecycle**       | `presentation_state`, `superseded_by_activity_id`                           | Which row is presented after a bridge. See the note below — mostly derivable.     |

Two things that would otherwise land here are deliberately kept out:

- **Load-rebuild bookkeeping lives in its own table**, not as a column on activity rows.
  See "Training load rebuild".
- **Athlete-authored values** (`rpe`, `athlete_notes`, `fatigue_notes`, `fueling_notes`)
  are not a fifth job. They are projected like everything else, from an
  `athlete_override` source.

**`presentation_state` is a cache, not independent truth.** Bridging reparents _every_
live source from the superseded activity to the survivor, so a superseded activity has
zero live sources — and recompose is already defined over
`activity_id = X and retired_at is null`. So `presentation_state = 'superseded'` is
equivalent to `not exists (select 1 from activity_sources where activity_id = X and
retired_at is null)`. It is stored anyway, because a `NOT EXISTS` on every calendar
query is the wrong read shape, but it is stored as a **derived cache with a tested
consistency invariant**, not as a fact the system could disagree with itself about.
`superseded_by_activity_id` is genuinely not derivable and stays as a pointer.

**Where the bridging complexity comes from.** The plan link is stored twice —
`activities.planned_workout_id` and `plan_workouts.actual_activity_id` — two columns
encoding one relationship, kept in agreement by an RPC that raises `22023` when they
disagree (`20260806003910_unlink_plan_workout_from_activity_atomic.sql:51-58`). That
denormalization predates this design. It is why transferring a link during a bridge is
the most dangerous operation here, and it is not inherent to merging. Out of scope to
fix; named so nobody mistakes the complexity for something this design introduced.

---

## Architectural decisions (settled — do not relitigate during execution)

**1. Not a Postgres `MATERIALIZED VIEW`.** RLS cannot be applied to a matview, and
`20260816191358_rls_and_security.sql` enabled RLS on every table — a matview would be a
security regression on the most sensitive table in the schema. Matviews also only
support full `REFRESH`, so one athlete's upload would rebuild the whole table. The
projection is maintained by RPC, which is the established house pattern: this schema has
zero views and zero triggers beyond `set_updated_at`, and every derived artifact
(`daily_load_snapshots`, `activity_summary`, `generation_context`) is computed in Python
and written explicitly.

**2. `activities` keeps its `id` and stays the row everything reads.**
`activity_sources` is a new child table with `activity_id → activities.id`.

Rejected alternative: a new `activity_groups` table with readers repointed at it.
Identical user-visible outcome, far larger blast radius — `plan_workouts.actual_activity_id`
FKs to `activities(id)`, and every index, reader, and the three existing plan-link RPCs
would have to be rewritten.

**3. Dedup uses two kinds of key, and only one kind may reject.**

- **Authoritative identifiers — `content_hash` (sha256 of the raw bytes) and a provider
  `external_id`.** These assert _identity_: this input **is** an input already stored.
  They may reject an ingestion outright, each enforced by its own partial unique index.
  An external id is authoritative only when its provider is known; `provider = 'unknown'`
  has no namespace and never participates in that key.
- **Heuristic evidence — `payload_fingerprint` (a hash over normalized extracted
  fields).** This asserts _similarity_, which is a judgement. It narrows the candidate
  set for the tiered merge, **never rejects anything**, and carries no unique index.

The asymmetry is a requirement, not an implementation detail, and the reasoning is in
"`payload_fingerprint` is candidate evidence, never a constraint" below. Every other
mention of it in this document refers back to that section rather than restating it.

**4. Exact duplicate → `409`, naming the existing activity.** "Exact" means an
authoritative identifier matched. The central `PostgRESTAPIError` handler already maps
SQLSTATE `23505` → 409 with a documented rationale (`api/index.py:135-181`).

**5. Two merge tiers.** Tier A auto-merges high-confidence matches; Tier B proposes to
the athlete and requires confirmation. Tier A may attach a new source to an existing
activity but may never combine two activities that already exist — see "Bridging".

**6. Derived values are rebuilt from the merged field set, never merged across sources.**
Covers `tss`, `intensity_factor`, `activity_summary`, and `summary_schema_version`. Two
qualifications, both in "Derived values" below: a value is only rebuilt when an input to
it actually changed, and a provider-supplied TSS is an input rather than something to
recompute over.

---

## Prerequisite bug fixes

Three defects block this work. All are live today, independent of deduplication, and
filed separately so they can ship without waiting for this design to be approved.

| Issue | Defect                                                                                                                                           | Why this design needs it                                                                                                                                          |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| #461  | `recompute_load_endpoint` seeds CTL/ATL from the **newest** snapshot regardless of `since`, and reads candidates through a 500-row display query | Merging changes historical daily TSS. Without both fixes a backward rebuild produces wrong numbers, so a merge cannot correct load — it can only move the error.  |
| #462  | `_activity_source_for_filename` can emit `"file_upload"`, which is not in `activities_source_check`, producing a CHECK violation surfaced as 503 | The same upload path is being rewritten here, and the backfill's source mapping is total over the _valid_ allow-list — a stored `file_upload` row would abort it. |
| #463  | No reader for a sport threshold as of a past date; `get_active_thresholds` returns current values only                                           | Recomputing a historical TSS uses today's FTP. Not a hard blocker — the rule in "Derived values" bounds the exposure — but it removes the imprecision properly.   |

### Sequencing

**#461 is a hard gate on Phase 4 and should ship well before it.** Phase 4 is the first
phase that merges anything, and every merge marks a load window stale. With #461
unfixed, each of those rebuilds seeds from the newest snapshot and reads through a
500-row cap, so the rebuild writes a _wrong_ series rather than a corrected one. That is
strictly worse than the double-counting this design exists to remove: today the error is
an over-count with an identifiable cause, whereas afterwards it would be an arbitrary
series with none. Do not ship Phase 4 against an unfixed #461 — and prefer to land #461
on its own, ahead of Phase 1, rather than carrying it as a Phase 0 task inside this work.
It is an independent bug fix with independent value, and holding it hostage to this
design's review cycle leaves a live defect shipped for no reason.

**#462 gates Phase 1.** The backfill's source mapping is total over the _valid_
`activities_source_check` allow-list, and its preflight aborts on any unmapped value. A
`file_upload` row that reached the table would therefore stop the backfill outright, and
the Phase 1 write path can still produce them until #462 lands.

**#463 gates nothing.** The rule in "Derived values" bounds the exposure to activities
whose inputs a merge actually changed, within the 90-day horizon. It should land when
convenient, not as a blocker.

---

## Data model

### New table: `activity_sources`

One row per ingested input. Evidence fields are immutable; two narrow lifecycle fields
are not, per "The evidence/state boundary". Migration file
`supabase/migrations/<ts>_activity_sources.sql`, all-lowercase, prose header naming the
issue — matching `20260708000000_intervals_connections.sql`.

```sql
create table public.activity_sources (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.athlete_profiles(user_id) on delete cascade,

  -- Composite FKs, not plain id references: they make "this source belongs to the
  -- same athlete as its activity" a database invariant rather than an RPC habit.
  -- Requires `alter table public.activities add constraint activities_id_user_id_key
  -- unique (id, user_id);` — redundant to the PK, and that is the point: it is what
  -- a composite FK can target. See "Cross-user references are closed by the schema".
  activity_id uuid not null,
  origin_activity_id uuid not null,
  foreign key (activity_id, user_id)
    references public.activities(id, user_id) on delete cascade,
  foreign key (origin_activity_id, user_id)
    references public.activities(id, user_id) on delete cascade,

  provider text not null check (provider in (
    'garmin','intervals','strava','wahoo','coros','polar','suunto','athlete','unknown'
  )),
  -- FK to the fidelity reference table rather than a CHECK list, so the permitted
  -- formats and their ranks are defined in exactly one place. There is deliberately
  -- no fidelity_rank column here — see "Source fidelity lives in a reference table".
  ingest_format text not null references public.activity_ingest_formats(ingest_format),
  external_id text,
  object_key text,
  content_hash text,
  payload_fingerprint text,
  fields jsonb not null default '{}'::jsonb,
  raw_extraction jsonb,
  recorded_at timestamptz,
  retired_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

-- Byte identity only. Deliberately not scoped by provider or ingest_format: both are
-- inferred labels, and no inferred field belongs in a key that can reject an upload.
create unique index activity_sources_content_hash_idx
  on public.activity_sources (user_id, content_hash)
  where content_hash is not null and retired_at is null;

-- Provider-issued identity. `unknown` is excluded because an identifier without a
-- namespace is not authoritative; two unrelated devices may emit the same value.
create unique index activity_sources_external_id_idx
  on public.activity_sources (user_id, provider, external_id)
  where external_id is not null
    and provider <> 'unknown'
    and retired_at is null;

-- Deliberately NOT unique; see "payload_fingerprint is candidate evidence".
create index activity_sources_fingerprint_idx
  on public.activity_sources (user_id, payload_fingerprint)
  where payload_fingerprint is not null and retired_at is null;

create index activity_sources_activity_idx
  on public.activity_sources (activity_id) where retired_at is null;

-- At most one live athlete override per activity. Retiring the prior override and
-- inserting the new one is the writer's job; this index makes a writer that forgets
-- fail loudly instead of silently resurrecting an older edit.
create unique index activity_sources_one_live_override_idx
  on public.activity_sources (activity_id)
  where ingest_format = 'athlete_override' and retired_at is null;

create trigger activity_sources_set_updated_at
before update on public.activity_sources
for each row execute function public.set_updated_at();
```

Plus `enable row level security` with the owner policy
(`(select auth.uid())::text = user_id`) and the standard
`revoke all … / grant … to service_role` block.

**Both authoritative-identity indexes are partial _indexes_, not constraints** —
deliberately. They exclude retired rows, which a constraint cannot. Nothing upserts
against them (we insert and let `23505` drive the recovery path in "Exact duplicate"),
so the PostgREST limitation that `on_conflict` cannot name a partial index does not
apply.

Three cases these keys must get right:

| Case                            | Behaviour               | Why                                                                                                                                                                                 |
| ------------------------------- | ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Re-upload the same ZIP          | every member rejected   | Members have distinct bytes → distinct `content_hash`; the archive's shared `object_key` is no longer a dedup key, so the constraint `20260716000000` had to work around disappears |
| Two distinct members of one ZIP | both stored             | Distinct bytes, distinct hashes                                                                                                                                                     |
| Text extract                    | never rejected as exact | No bytes ⇒ `content_hash` NULL, and NULLs are distinct in a unique index. A fingerprint may still be computed — it just cannot reject                                               |

### Source fidelity lives in a reference table, not on the row

Fidelity ordering decides which source wins a contested field, so a row carrying its own
rank is a row that can carry a _wrong_ rank. A plain `integer not null` column would
accept `('fit', 7)` — a FIT source ranked below a manual entry — and the failure is silent
in the worst way: the reconciler sorts by rank, the manual entry's numbers overwrite the
FIT's, and the athlete sees a ride with wrong power and no obvious cause. One writer
inserting a stale constant is enough to cause it.

Rank is a property of the **format**, not of the row, so it is stored that way:

```sql
create table public.activity_ingest_formats (
  ingest_format text primary key,
  fidelity_rank integer not null unique,
  created_at timestamptz not null default timezone('utc', now())
);

insert into public.activity_ingest_formats (ingest_format, fidelity_rank) values
  ('athlete_override', 0),
  ('fit',              1),
  ('tcx',              2),
  ('gpx',              3),
  ('intervals_api',    4),
  ('text',             5),
  ('screenshot',       6),
  ('manual',           7);
```

`activity_sources.ingest_format` is a foreign key to it, and there is no `fidelity_rank`
column on `activity_sources` at all. The per-row staleness hazard is not policed — it
becomes unrepresentable, because there is no per-row rank to be stale.

Three further consequences, all of which favour this over a generated column:

- **The format allow-list exists once.** A generated column would have required the eight
  formats in a `CHECK` constraint _and_ in a `case` expression, kept in agreement by
  review. Here the primary key is the allow-list, and the FK enforces it.
- **Changing a rank is a one-row `UPDATE`.** No table rewrite, no `ACCESS EXCLUSIVE` lock.
  Adding a format is one `INSERT` rather than a `CHECK` migration plus a
  generated-expression migration. (All three Supabase projects are on Postgres 17 — local
  `supabase/config.toml:36`, preview 17.6.1.105, production 17.6.1.155 — so
  `alter column … set expression` was available; it is simply the worse tool here.)
- **The `unique` on `fidelity_rank` makes ties impossible**, which matters because the
  reconciler's tie-break assumes a total order over formats before it ever reaches
  `created_at`.

`activity_ingest_formats` is reference data, not athlete data: it carries no `user_id`,
gets `select` for `authenticated` and `service_role`, and `insert`/`update`/`delete`
revoked from application roles so the mapping only changes by migration. RLS is enabled
with a permissive read policy rather than left off, so the table matches the
all-tables-have-RLS posture established by `20260816191358_rls_and_security.sql`.

**The reconciler resolves rank by lookup, not by row.** It loads the eight rows once per
process and sorts sources by `(fidelity_rank[ingest_format], created_at, id)`. Since
`ingest_format` is immutable evidence (see the evidence/state boundary), a source's
effective rank can only change when the _mapping_ changes — which is a reviewed migration,
and which is the point: re-ranking a format is a deliberate, auditable act rather than
something a writer can do by accident.

### Cross-user references are closed by the schema, not by the RPCs

`user_id` and `activity_id` constrained independently permit a row that names one athlete
and points at another athlete's activity. RLS does not close this: the owner policy tests
`user_id` alone, so a mislabelled row is perfectly visible to the athlete it names while
carrying someone else's data. Neither does the service-role write path — every RPC takes
`p_user_id` from `require_user_context()` and never from a request body, but that is a
property of code that must be re-established in each new RPC, and this design adds
several.

Composite foreign keys make it structural. `(activity_id, user_id)` can only resolve to a
row of `activities` carrying the same `user_id`, so a cross-user reference is not
rejected at review time or request time — it is unrepresentable. The redundant
`unique (id, user_id)` on `activities` exists solely to give those FKs a target. RPC-level
owner checks remain, because a caller must still be denied the _existence_ of another
athlete's activity, but they are no longer the only thing between a bug and a tenancy
leak.

The same treatment applies to `activities.superseded_by_activity_id`, which otherwise
lets one athlete's activity be superseded by another's:

```sql
alter table public.activities
  add constraint activities_superseded_by_same_user
  foreign key (superseded_by_activity_id, user_id)
  references public.activities(id, user_id) on delete set null;
```

### Scoping the byte-identity key: `(user_id, content_hash)` and nothing else

Neither `provider` nor `ingest_format` appears in it. This is separate from
`(user_id, provider, external_id)`, where provider belongs because it is the namespace
that makes an external id authoritative.

**Why not `ingest_format`.** Identical bytes cannot be two different recordings. If the
same file arrives once named `ride.fit` and once named `ride.gpx`, the athlete uploaded
one file twice, and rejecting the second is correct. This does not touch the genuine
same-ride-two-formats case: a Garmin export produced as `.fit` and as `.gpx` contains
**different bytes**, so it has a different hash, is never rejected, and is stored as two
sources that merge on their fields.

**Why not `provider`.** There is no `provider` column anywhere today; every upload path
derives only a _format_ from the filename suffix (`api/index.py:1513`), so on day one of
Phase 2 essentially every upload lands in `provider = 'unknown'`. Including provider
would mean the same bytes re-uploaded under a later, better-inferred label no longer
collide with the earlier row — an inferred label quietly weakening a key whose whole job
is byte identity.

**The rule underneath both.** Narrowing a rejection key produces a missed duplicate;
widening one produces a refused workout. `(user_id, content_hash)` is the widest scope
that is still a byte-identity claim, and **no field that is inferred rather than derived
from the bytes may enter it**.

### `payload_fingerprint` is candidate evidence, never a constraint

**Requirement.** The fingerprint may narrow the candidate set for the tiered merge. It
may **not** reject an ingestion, and it carries no unique index. Only `content_hash` or a
known-provider `external_id` may reject.

This is not a tuning preference a cleverer normalization could overturn. The fingerprint
hashes rounded, provider-disagreeing values by construction — distance rounded to 10 m,
`started_at` truncated to the second, elevation excluded. Rounding is what makes it
useful for _matching_ and exactly what makes it unsound for _identity_: two genuinely
different sessions that round together are indistinguishable from one session recorded
twice, and endurance training supplies those collisions readily. An athlete repeating a
fixed 60-minute trainer session at the same time on consecutive days, or riding two loops
of one circuit, produces near-identical normalized fields.

Both failures are wrong; they are not equally wrong:

| Failure                                  | Cost                                                                                    | Recoverable?                                                 |
| ---------------------------------------- | --------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| Fingerprint misses a real duplicate      | A visible duplicate the athlete can see and the coach can merge                         | Yes — Tier B exists for exactly this                         |
| Fingerprint rejects a legitimate session | A workout the athlete did is never stored; load and compliance are quietly short by one | **No.** Nothing was written, so there is nothing to un-merge |

A unique index converts the second row into data loss at upload time, before the athlete
has any surface on which to disagree. Everything downstream here — the merge tiers, the
confirmation, the un-merge path — exists because similarity judgements need to be
reversible; putting one behind a constraint puts it outside that machinery.

**What it is for instead.** An index-backed prefilter for `list_dedup_candidates`. An
exact fingerprint match promotes a pair for scoring, but the pair still passes through the
Tier A/B predicate, the hard negatives, and — for Tier B — athlete confirmation. Nothing
merges because two fingerprints are equal; something merges because the scorer said so on
the underlying fields.

Because it enforces nothing, its scoping is a recall question, and it is
`(user_id, payload_fingerprint)` — not scoped by provider or format, since matching a
Garmin FIT to the Intervals record of the same ride is the case this design exists to
catch.

**What it hashes.** `sport`, `started_at` truncated to the second, `duration_seconds`, and
`distance_meters` rounded to 10 m. `elevation_gain_meters` is excluded: Garmin's
barometric elevation and a GPX's DEM-derived elevation for the _same ride_ routinely
differ by tens of metres, so including it would guarantee the two never match.

**It is NULL unless it carries enough information to be worth a bucket:** `started_at`
plus at least one positive metric. Without a start instant the remaining fields are far
too weak — an athlete who runs 45 easy minutes twice in one day produces two identical
field sets, and doubles are ordinary in endurance training. Sparse rows would land in a
bucket the scorer then has to score pairwise, which is the cost the prefilter exists to
avoid. A NULL fingerprint loses a prefilter, not a code path: such rows are still reachable
through the date-windowed candidate query and scored on their fields like anything else.

Per AGENTS.md a GPX _recording_ always spans a positive interval, and a file spanning none
is classified as a course and never reaches `activities`, so files normally carry one.

### The evidence/state boundary, enumerated and enforced

"One immutable row per ingested input" is too loose to build against, because the same row
carries lifecycle fields that must change. So the boundary is enumerated and enforced in
the database rather than by convention.

**Requirement.** Values received from an ingestion source are never rewritten in place.
Every field that resolves, retires, supersedes, or overrides those values lives in the
narrow mutable set below and nowhere else.

| Class                                                               | Fields                                                                                                                                                                                          | Rule                                                                                  |
| ------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| **Immutable evidence** — what this input said                       | `id`, `user_id`, `origin_activity_id`, `provider`, `ingest_format`, `external_id`, `object_key`, `content_hash`, `payload_fingerprint`, `fields`, `raw_extraction`, `recorded_at`, `created_at` | Written once at insert. **Any `UPDATE` that changes one is rejected by the database** |
| **Mutable state** — how the system currently resolves that evidence | `activity_id` (group membership), `retired_at` (lifecycle)                                                                                                                                      | Written only by the merge/bridge/retire RPCs, under the documented lock order         |
| **Trigger-managed**                                                 | `updated_at`                                                                                                                                                                                    | `set_updated_at`, as everywhere else in this schema                                   |

Two mutable columns is the entire surface. A future requirement needing a third gets added
to this table and to the guard in the same change, or it does not ship.

```sql
create or replace function public.activity_sources_reject_evidence_update()
returns trigger language plpgsql
set search_path = '' as $$
begin
  -- Evidence is what the ingested input said; only membership and lifecycle move.
  if (to_jsonb(new) - 'activity_id' - 'retired_at' - 'updated_at')
     is distinct from
     (to_jsonb(old) - 'activity_id' - 'retired_at' - 'updated_at') then
    raise exception 'activity_sources evidence fields are immutable'
      using errcode = '22023';
  end if;
  return new;
end;
$$;

create trigger activity_sources_evidence_immutable
before update on public.activity_sources
for each row execute function public.activity_sources_reject_evidence_update();
```

Subtracting the mutable keys rather than listing the immutable ones is deliberate: a column
added later is immutable by default, so forgetting to update the guard fails closed. The
trigger binds the `security definer` RPCs too, which is the point — they are the only
writers, and column-level `revoke update` would not reach them.

**No evidence column may carry `on update cascade` or `on delete set null`.** Either is an
`UPDATE`, so it fires this guard, raises `22023`, and aborts the parent operation.
`origin_activity_id` is `on delete cascade` because that is the only action which does not
rewrite the row.

That cascade has a consequence. After a bridge a source can have `activity_id = A` while
`origin_activity_id = B`; deleting B would cascade the source away even though it is a live
member of A, and A would then recompose one source short. Nothing reaches this today — the
repo has no `delete_activity` and this design adds none — but **any future
individual-activity delete must be group-aware**, reparenting or retiring the sources it
would orphan.

**Athlete overrides are append-only.** An edit inserts a new `athlete_override` source and
retires the previous one in the same transaction, so at most one is ever live. Recompose is
unchanged — it reads the live set — and edit history comes free.

`activity_sources_one_live_override_idx` makes that two-step enforceable. A half-applied
edit leaves two live rank-0 sources, and since the tie-break prefers the earlier
`created_at`, the athlete's **older** edit would win and their correction would appear
ignored. The index turns that into a `23505` immediately. This is not a rejection key in
the sense the scoping rule forbids: it constrains a row the athlete's own edit created, the
recovery is to retire the previous override and retry, and no ingested workout can be lost
to it.

**Accepted consequence.** Because `provider` and `ingest_format` are evidence, improving
provider inference later cannot re-label rows in flight — it takes a backfill migration,
reviewed as one. That is the intended trade: a label that can silently change underneath a
fidelity ordering can silently change which source won a field. Note this is why
`ingest_format` being immutable matters more than it looks: it is the key that resolves a
source's rank, so freezing it freezes the row's position in the merge order.

### Changes to `activities`

```sql
alter table public.activities
  add column source_count integer not null default 1,
  add column field_provenance jsonb not null default '{}'::jsonb,
  add column presentation_state text not null default 'active'
    check (presentation_state in ('active','superseded')),
  add column superseded_by_activity_id uuid
    references public.activities(id) on delete set null,
  add column materialized_at timestamptz;
```

**The self-FK must be `on delete set null`, not `restrict`.** `activities.user_id` is
`on delete cascade` from `athlete_profiles`, and `restrict` does _not_ yield to a cascading
parent delete — so a `restrict` self-FK would make account deletion fail outright for any
athlete who has ever had a superseded activity.

There is no `load_rebuild_pending_from` column here; see "Training load rebuild".

---

## How it works

### N = 1 (the common case)

1. File arrives at `process_uploaded_file_endpoint` (`api/index.py:1623`), parsed by
   `parse_gpx`/`parse_fit`/`parse_tcx`. A `ParsedCourse` still returns without persisting
   anything (unchanged AGENTS.md invariant).
2. Compute `content_hash` from the bytes already in hand and `payload_fingerprint` from
   the parsed fields.
3. Look for a grouping candidate. None found.
4. Insert one `activities` row **and** one `activity_sources` row in a single RPC.
   `source_count = 1`, `field_provenance` maps every field to that source id.
5. `_finalize_persisted_activity` → `_try_match_activity_to_plan` as today.

The materialized row is byte-for-byte what it is today. Nothing about the calendar,
compliance, or the coach changes for single-source activities.

### Exact duplicate

Steps 1–2 identical, then **select-then-insert**. Only authoritative identifiers are
consulted; `payload_fingerprint` is deliberately absent, because this is the one path that
can refuse to store an athlete's workout.

1. Run the applicable identity lookups: `(user_id, content_hash)` when bytes exist, and
   `(user_id, provider, external_id)` only when both an external id and a provider other
   than `unknown` exist. Both filter `retired_at is null`.
2. If every match names the same activity → return **409** naming that `activity_id`. If
   byte identity and provider identity resolve to different activities, return 409 naming
   both and store nothing: the authoritative evidence is inconsistent and must not be
   guessed into one group.
3. Otherwise insert. The two partial unique indexes are the **race backstops**. On a
   concurrent double-submit the insert raises `23505`, caught locally and documented at the
   catch site per AGENTS.md. Recovery is allow-listed by constraint name: only
   `activity_sources_content_hash_idx` and `activity_sources_external_id_idx` trigger the
   corresponding identity lookup. A `23505` from
   `activity_sources_one_live_override_idx`, or any unknown constraint or other database
   error, propagates unchanged. If the two recovery lookups disagree, use the same
   conflicting-identity 409.

The lookup is required, not belt-and-braces: a `PostgRESTAPIError` for `23505` carries the
constraint name and a detail string, **not** the colliding row — so the central handler
alone can only produce a bare conflict, never _"you already logged this ride on June 1."_
Two round trips is the honest cost of a useful message.

**Un-merge does not make the same source ingestible again.** Un-bridging restores source
membership and leaves the sources live, so a byte-identical re-upload still returns 409
naming the restored activity. Both identity indexes are partial on `retired_at is null`,
which would permit a future explicit source-retirement flow; **this design exposes no such
flow**, and nothing in it depends on one.

### N > 1 (merge)

A new source arrives and matches an existing activity. It is inserted with `activity_id`
pointing at that activity, then `recompose_activity` recomputes the projection from the
full live source set.

Worked example — a Garmin FIT upload of a ride already synced from Intervals.icu:

```text
activity_sources                          activities  (the projection)
─────────────────────────────────────     ─────────────────────────────────────
S1 intervals / intervals_api  rank 4      sport            cycling
   started_at  09:02:11                   started_at       09:02:07   ← S2 (rank 1)
   duration    3607                       duration_seconds 3612       ← S2
   distance    42180                      distance_meters  42184      ← S2
   avg_hr      —                          avg_hr_bpm       148        ← S2
   tss         71                         avg_power_watts  212        ← S2
                                          tss              71         ← S1 (provider)
S2 garmin / fit               rank 1      rpe              7          ← S3
   started_at  09:02:07                   athlete_notes    "legs ok"  ← S3
   duration    3612                       source_count     3
   distance    42184                      presentation_state active
   avg_hr      148
   avg_power   212                        field_provenance {"avg_hr_bpm": "S2", …}

S3 athlete / athlete_override rank 0
   rpe 7, athlete_notes "legs ok"
```

Both device rows stay intact and immutable. The athlete sees one ride. Note `tss` comes
from S1 rather than being recomputed — see "Derived values".

---

## Field-level merge precedence

Source fidelity, highest first:

```text
athlete_override (0) > fit (1) > tcx (2) > gpx (3) > intervals_api (4)
                     > text (5) > screenshot (6) > manual (7)
```

After fidelity, a total and stable tie-break: earlier `created_at`, then lexicographically
smaller `id`. The reconciler sorts the complete source set by this order every time and
never relies on call order or JSON iteration order.

| Field group                                                                                                                                                                                                           | Rule                                                                                                                   |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Measurements — `duration_seconds`, `distance_meters`, `elevation_gain_meters`, `avg_hr_bpm`, `max_hr_bpm`, `avg_power_watts`, `normalized_power_watts`, `avg_pace_sec_per_km`, `avg_cadence_rpm`, `zone_distribution` | First non-null value in fidelity order                                                                                 |
| `started_at`, `activity_date`                                                                                                                                                                                         | Highest-fidelity source that has one — a FIT timestamp is authoritative                                                |
| `sport`                                                                                                                                                                                                               | Highest-fidelity source that declares a real sport. `"general"` is treated as _undeclared_, not as a conflicting value |
| Derived — `tss`, `intensity_factor`, `activity_summary`, `summary_schema_version`                                                                                                                                     | See "Derived values" below                                                                                             |
| Athlete-authored — `rpe`, `athlete_notes`, `fatigue_notes`, `fueling_notes`                                                                                                                                           | Exact four-key value from the one live `athlete_override`; all four become NULL when no live override exists           |
| `source`, `source_file_key`, `raw_extraction`                                                                                                                                                                         | Copied as one triplet from the first live non-override source in the same total fidelity order; see below              |
| `planned_workout_id`                                                                                                                                                                                                  | Never field-merged. Transferred only by the explicit link RPC under lock                                               |

### Derived values

`tss`, `intensity_factor`, `activity_summary`, and `summary_schema_version` are rebuilt
from the merged field set rather than merged across sources. Deep-merging summaries from
disagreeing sources can produce a summary that contradicts the fields it claims to
summarize; `build_activity_summary_from_fields` (`supabase_repo.py:255`) already derives
one from fields, so it is called once on the resolved set.

Two qualifications.

**A derived value is only rebuilt when an input to it actually changed.** Recompose
recomputes `tss` and `intensity_factor` only when the merged field set changes a TSS input
— sport, duration, normalized power, average pace, average HR, or rpe. Otherwise the
stored value is carried forward untouched.

This matters because **recompose is not a pure function of the live source set alone**. It
is a function of (live source set × the athlete's threshold state). `compute_tss`
(`backend/engine/tss.py`) needs `ftp`, `threshold_pace_sec_km`, `max_hr`, `resting_hr`, and
`biological_sex`, none of which live in `activity_sources`. Without this rule, recomposing
a months-old activity would restate its historical training load under today's FTP for
reasons unrelated to the merge that triggered it — which is the harm this design refuses at
backfill time, reintroduced through the front door.

Until #463 lands there is no reader for a threshold as of a past date, so when recompose
_does_ recompute, it uses current thresholds. The exposure is bounded: only activities whose
inputs a merge actually changed are affected, and the 90-day rebuild horizon caps the visible
effect on CTL at roughly 0.27 TSS-equivalent for a 100-TSS error — below what an athlete
perceives.

**A provider-supplied TSS is an input, not something to recompute over.**
`backend/services/intervals.py:367` takes `icu_training_load` verbatim from the provider.
Intervals computes it from the full activity stream; we would recompute from a rounded
summary NP, which is very likely worse. So a source that supplies a TSS contributes it as a
value in fidelity order, and we compute our own only when no source supplies one.

This is an **interim rule, not a trust judgement**. A provider TSS is not a measurement — it
is another party's model output arriving over a high-fidelity channel, and the fidelity
ladder ranks closeness-to-sensor rather than confidence in a computed number. #464 tracks
replacing this with a calibrated decision: comparing the two populations, scoring our own
confidence by input completeness, and deriving per-provider (and where warranted,
per-provider-per-athlete) trust. Because sources are immutable and retain their own
`fields`, that comparison can be run retroactively over accumulated data — nothing extra
needs storing now.

### The legacy provenance triplet

`source`, `source_file_key`, and `raw_extraction` are selected from **one** source rather
than mixed. Ignore `athlete_override` rows, then take the first live source in the ordinary
fidelity, `created_at`, `id` order; map its `ingest_format` back to the existing
`activities.source` vocabulary (`fit → fit_upload`, `gpx → gpx_upload`, `tcx → tcx_upload`,
`intervals_api → intervals_sync`, `text → text_extract`,
`screenshot → screenshot_extract`, `manual → manual`), copy `object_key` to
`source_file_key`, and copy its `raw_extraction`. Retiring that winner deterministically
promotes the next live source; restoring it promotes it again. If no non-override source
exists, recompose raises `22023` rather than manufacturing provenance.

Full provenance and every external id remain in `activity_sources`, so correctness must
never depend on this lossy triplet. In particular, `list_synced_intervals_keys` moves to a
keyset-paginated query over non-retired
`activity_sources(provider='intervals', ingest_format='intervals_api')`.

### Retiring the legacy Intervals identity

Once that source-backed reader and the external-id index are live, Phase 2 drops both
`activities_intervals_source_file_key_unique` and the generated
`activities.intervals_source_file_key` column. Leaving them through Phase 4 is incorrect: a
superseded Intervals-origin row can retain `intervals:{id}` while survivor recomposition
selects the reparented Intervals source and projects the same key, making the bridge fail
with `23505`. Source identity now owns that invariant. Phase 4 has a migration preflight
that aborts if the legacy constraint or column still exists.

**The Intervals write path must be converted in the same phase, not just the read path.**
`create_intervals_activity` (`backend/repos/supabase_repo.py:444-460`) is an upsert whose
conflict target _is_ that constraint:

```python
.upsert(payload, on_conflict="user_id,intervals_source_file_key", ignore_duplicates=True)
```

Dropping the constraint without replacing this leaves PostgREST with no conflict target and
breaks the sync write path. Intervals sync gets a **canonical ingestion entry point** in
Phase 2 — the same `create_activity_with_source` RPC every other path uses, with the
Intervals activity id supplied as a real `external_id` under `provider = 'intervals'` — so
idempotency comes from `activity_sources_external_id_idx` rather than from a generated
column on the projection. That refactor is a required part of Phase 2, not a follow-up.

### Projection ownership is exhaustive and RPC-enforced

There is no catch-all. Every `activities` column belongs to exactly one writer class:

| Writer class                         | Complete column set                                                                                                                                                                                                                                                                                                                         |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Insert identity, then immutable      | `id`, `user_id`, `created_at`                                                                                                                                                                                                                                                                                                               |
| Reconciled source projection         | `sport`, `activity_date`, `started_at`, `duration_seconds`, `distance_meters`, `elevation_gain_meters`, `avg_hr_bpm`, `max_hr_bpm`, `avg_power_watts`, `normalized_power_watts`, `avg_pace_sec_per_km`, `avg_cadence_rpm`, `zone_distribution`, `source`, `source_file_key`, `raw_extraction`, `summary_schema_version`, `activity_summary` |
| Rebuilt by the reconciler            | `tss`, `intensity_factor`                                                                                                                                                                                                                                                                                                                   |
| Athlete override, via the reconciler | `rpe`, `athlete_notes`, `fatigue_notes`, `fueling_notes`                                                                                                                                                                                                                                                                                    |
| Recompose metadata                   | `source_count`, `field_provenance`, `materialized_at`                                                                                                                                                                                                                                                                                       |
| Plan-link RPCs only                  | `planned_workout_id`                                                                                                                                                                                                                                                                                                                        |
| Bridge/un-bridge RPCs only           | `presentation_state`, `superseded_by_activity_id`                                                                                                                                                                                                                                                                                           |
| Trigger-managed                      | `updated_at`; the legacy generated `intervals_source_file_key` is removed in Phase 2                                                                                                                                                                                                                                                        |

**Enforcement before Phase 4.** After Phase 3 converts the last direct writer, revoke direct
`UPDATE` on `activities` from `service_role`, `authenticated`, and `anon`. Updates then run
only through the locked `security definer` RPCs. Each RPC uses an explicit `SET` list limited
to its writer class; `recompose_activity` owns the projection, rebuild, override, and metadata
rows, and its explicit set writes all four athlete columns on every call, using NULLs when no
override exists. A guard trigger independently rejects changes to `id`, `user_id`, or
`created_at`, including from a definer RPC. Table-owner migrations remain the only escape
hatch and are reviewed as migrations.

Any new `activities` column must be added to this matrix, one RPC's explicit write set, and
the negative database tests in the same change. An unassigned column blocks the migration; it
does not inherit an "untouched" default.

### Athlete edits as an override source — and the Phase-3 ownership gate

Modelling an athlete edit as a `provider='athlete'` source row makes recompose a total
function over sources. Every override stores all four athlete fields using explicit JSON
nulls; a partial edit first carries forward the other three current values, so omission never
ambiguously means either "preserve" or "clear".

`activities.rpe` / `athlete_notes` are written directly **today** by `repo.update_activity` and
`merge_activity_text_update` (`backend/services/activity_text.py`). Recompose cannot own those
columns until that legacy state has provenance. Phase 3 first inserts one complete override for
every activity with any non-null athlete field, including values emitted by
`build_activity_from_text`; activities with no override must already have all four columns
NULL. It then converts all three writers and verifies this invariant before revoking direct
updates and enabling any general recompose path.

After that gate, recompose always owns all four columns: a live override supplies the exact
four-key value, and no live override clears all four to SQL NULL. "Leave the projection
untouched" is forbidden after Phase 3 — otherwise a one-sided override bridge would copy B's
notes onto A, and un-bridge would return the source to B while leaving those notes stranded.

The ordering also protects the rest of the row. `merge_activity_text_update` handles date
corrections today by calling `repo.update_activity`, which writes the whole `Activity` model
including derived and device fields. If Tier-A merge shipped first, recompose could silently
revert that correction. Phase 3 must therefore finish the provenance conversion and writer
revocation before Phase 4 runs; this is a release gate, not a transitional fallback inside the
reconciler.

---

## Grouping predicate

The scorer lives in a new `backend/services/activity_dedup.py`, **pure and
side-effect-free**, mirroring `match_activities_to_workouts` (`compliance.py:85`). It takes
candidate sources/activities and returns proposed groups; it persists nothing and is testable
without a database.

| Tier  | Condition                                                                                                                                                                                                                                                                                                                                                          | Action                  |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------- |
| **A** | Casefolded sport equal (or one side `general`); both `started_at` present and within **±10 min**; both `duration_seconds` present, positive, and within **±5%**; and **no metric disagreement** — for distance, either both positive and within **±5%**, or both zero, or at least one null. A positive value against a zero is a mismatch and disqualifies Tier A | Auto-merge at ingestion |
| **B** | Same sport (or `general`); `activity_date` within **±1 day**; duration **or** distance within **±10%** over whichever both carry                                                                                                                                                                                                                                   | Propose to the athlete  |
| —     | anything else                                                                                                                                                                                                                                                                                                                                                      | Not a group             |

±1 day matches `MATCH_MAX_DAY_OFFSET = 1` (`compliance.py:24`); casefolded sport equality
matches `_pair_score` (`compliance.py:53`).

**Null degradation.** A missing `started_at` or `duration_seconds` on either side prevents
Tier A and routes the pair to Tier B. In Tier B a field null on one side is skipped rather
than scored as a mismatch — but Tier B still requires at least one actual metric agreement,
since same-sport-same-day alone describes two genuinely different workouts just as well as it
describes a duplicate.

**Zero is a value, not a null.** The comparator is `|a − b| ≤ tol × max(a, b)`, so no
percentage comparison ever divides. Every metric resolves to exactly one of three outcomes:

| Pair              | Outcome                  | Effect                                                                          |
| ----------------- | ------------------------ | ------------------------------------------------------------------------------- |
| both positive     | compared against `tol`   | agreement or mismatch                                                           |
| both zero         | **no comparable metric** | drops out exactly as a null does; the pair must find agreement elsewhere        |
| one zero, one not | **mismatch**             | disqualifies Tier A outright; in Tier B it counts against the pair, not skipped |

This matters because a trainer ride or a pool swim legitimately records
`distance_meters = 0` on both sides, and zero — unlike null — passes a naive "both rows carry
it" test. Treating `(0, 0)` as agreement would let same-sport-same-day form a group; treating
`0` against `5000 m` as skippable would auto-merge a trainer ride and an outdoor ride of the
same duration, and Tier A is the one tier with no athlete confirmation to catch it.
`duration_seconds` is held to the same rule and additionally must be positive on both sides
for Tier A: a zero-duration recording carries no evidence that it is the same session as
anything.

### Hard negatives — never grouped, at any tier

- **Two sources sharing a ZIP archive's `object_key`.** Every member of one archive shares it
  (`api/index.py:1786-1789`), so this reliably means _two distinct workouts uploaded
  together_. Accepted limitation: a ZIP containing the same ride as both `.fit` and `.gpx`
  will never merge. Weakening it reopens the false positive that actually costs the athlete a
  workout.
- **Already linked to different plan workouts.** The athlete or coach has asserted these are
  separate sessions.
- **Sport conflict between equal-fidelity sources.**

### Sport disagreement

Sport is a **hard casefolded equality gate** in `_pair_score` (`compliance.py:53`), so a wrong
merged sport makes an activity silently fail to match its planned workout — AGENTS.md already
documents this as a known failure mode.

Sport comes from the highest-fidelity source that declares one. If two sources of **equal**
fidelity declare _different_ sports, that is a hard negative — do not group them at all. Never
guess.

`"general"` — the honest fallback AGENTS.md mandates when a file does not declare a type — is
treated as _undeclared_, so a `general` source may join a `cycling` activity and two `general`
sources may group. It is not a sport that can conflict.

---

## Tier-B consent is persisted and enforced

A tool argument such as `confirmed: true` is not consent. Neither is confirmation text copied
into a tool call by the model. Tier B uses a short-lived, group-scoped proposal plus a
confirmation that is read from the athlete's own persisted chat turn.

```sql
create table public.activity_merge_proposals (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.athlete_profiles(user_id) on delete cascade,
  expected_phrase text not null,
  override_resolution jsonb,
  expires_at timestamptz not null,
  confirmed_message_id uuid,
  consumed_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  unique (id, user_id),
  check (expires_at > created_at),
  check (expires_at <= created_at + interval '15 minutes'),
  check ((confirmed_message_id is null) = (consumed_at is null))
);

create table public.activity_merge_proposal_members (
  proposal_id uuid not null,
  user_id text not null,
  activity_id uuid not null,
  expected_activity_updated_at timestamptz not null,
  expected_source_versions jsonb not null,
  primary key (proposal_id, activity_id),
  foreign key (proposal_id, user_id)
    references public.activity_merge_proposals(id, user_id) on delete cascade,
  foreign key (activity_id, user_id)
    references public.activities(id, user_id)
);

create unique index activity_merge_proposals_confirmation_once_idx
  on public.activity_merge_proposals (confirmed_message_id)
  where confirmed_message_id is not null;

alter table public.activity_merge_proposals
  add foreign key (confirmed_message_id, user_id)
  references public.chat_messages(id, user_id);
```

The migration adds a redundant `unique (id, user_id)` to `chat_messages`, as it does for
`activities`. `chat_messages` has only a primary key on `id` today (`0001_schema.sql:427`),
and adding the composite unique does not disturb the existing `on_conflict="id"` upsert in
`create_chat_message` (`supabase_repo.py:1059-1064`). Both tables have owner RLS; application
roles can read their own proposal but cannot insert or update proposal state, and only the
proposal-creation and merge definer RPCs write them.

**The confirmation is read from `chat_messages` directly — there is no separate attestation
table.** An earlier draft copied the user's normalized text into a third table. Every column of
it already exists on `chat_messages`, and the text is a pure function of `parts`, so it was a
materialized copy of one column rather than a normalization. Reading the canonical row is both
smaller and more trustworthy, since a copy can drift from what the athlete actually sent.

Two properties make this safe, both verified against current code:

- **The user's turn is committed before the model runs.** `app/api/chat/route.ts:292-294`
  awaits `persistUserMessage` before `streamCoachTurn` at `:302`.
- **The persisted text cannot be authored by the model.** `ChatPersistRequest`
  (`backend/models/chat.py:77-82`) accepts only `id`, `role`, `parts`, `metadata`, and
  `attachments`; the `role` CHECK admits only `'user'` and `'assistant'`
  (`0001_schema.sql:430`). Assistant turns are persisted separately, in the stream's
  `onFinish`.

Normalization is a single shared SQL function, used by proposal issuance and by the merge RPC
so the two definitions cannot diverge. It reads `parts`, the canonical column, rather than the
`content` mirror that is slated for removal:

```sql
create or replace function public.chat_message_normalized_text(p_parts jsonb)
returns text language sql immutable
set search_path = '' as $$
  select lower(btrim(regexp_replace(
    coalesce((select string_agg(part->>'text', '' order by ord)
              from jsonb_array_elements(p_parts) with ordinality as t(part, ord)
              where part->>'type' = 'text'), ''),
    '\s+', ' ', 'g')));
$$;
```

This is the same concatenation `create_chat_message` performs in Python
(`supabase_repo.py:1048`), expressed in SQL against the canonical column. Server-issued phrases
are ASCII, so Unicode case-fold ambiguity cannot change the comparison.

**Issuing a proposal.** The server receives a scorer-produced group, sorts and deduplicates its
activity ids, requires at least two active same-user members, and captures each activity's
`updated_at` plus the complete live source-version map. It issues a cryptographically random
code and stores the normalized ASCII phrase `merge activities <code>`. `expires_at` is at most
15 minutes after `created_at`. If the group has multiple live athlete overrides,
`override_resolution` contains the exact resolved athlete-field object shown to the athlete;
otherwise it is NULL. Unknown keys are rejected and explicit nulls preserved. The phrase shown
to the athlete describes that resolution, so consent binds the group and the override choice
together.

**Consuming it.** `merge_activity_group(p_user_id, p_proposal_id, p_confirmation_message_id)`
receives `p_user_id` from auth and the message id from trusted run context; the message id is
absent from the model-visible tool schema. In one transaction it:

1. locks the proposal and members, discovers the complete affected set, then locks every
   affected `plan_workouts` row by ascending id, every `activities` row by ascending id, and
   every live `activity_sources` row by ascending id — preserving the established
   `plan_workouts → activities → activity_sources` order for the whole group rather than pair
   by pair;
2. rejects a wrong user, expired or already-consumed proposal, a confirmation message created
   before the proposal or after expiry, a missing or non-`user` message, or normalized text
   unequal to `expected_phrase` — all before any merge write;
3. requires the active group membership and every activity/source version to equal the stored
   snapshot; any stale or substituted member raises `40001` without writing;
4. applies every pairwise bridge needed to collapse the **entire stored group** inside this
   transaction — callers cannot add or omit a member — including the stored override
   resolution and append-only event for each bridge; and
5. only after the final bridge leaves one survivor, sets `confirmed_message_id` and
   `consumed_at` together and commits.

Any failure rolls back the whole group, so no externally visible half-consumed proposal exists.
Group merging is internally pairwise but externally one atomic RPC, which satisfies the
one-tool-call limit without making consent reusable between requests. A timeout after commit is
recovered by reading merge events by `proposal_id`; the service does not re-consume the
proposal. A second consumption, even with the same message, is rejected.

**Persistence of the user turn is best-effort, and that fails closed.** `persistUserMessage`
swallows all errors so a failed write never blocks the coach turn
(`app/api/chat/route.ts:177-186`). If the row is missing, the confirmation lookup finds nothing
and the merge is rejected; the athlete repeats the phrase. This must never be "fixed" by
falling back to a model-supplied argument — that is the exact substitution this protocol
exists to prevent.

---

## The recompose RPC

Following `20260806003910_unlink_plan_workout_from_activity_atomic.sql`: `security definer`,
`set search_path = ''` fully qualified, `p_`-prefixed args, composite row return, documented
lock order, explicit revoke/grant.

```sql
public.recompose_activity(
  p_user_id text,
  p_activity_id uuid,
  p_fields jsonb,
  p_field_provenance jsonb,
  p_expected_source_versions jsonb,   -- {source_id: updated_at}
  p_expected_activity_updated_at timestamptz
) returns public.activities
```

- **Lock order: plan_workouts → activities → activity_sources**, matching the
  workout-then-activity order the existing RPCs document.
- Compares the live non-retired source set and their `updated_at` values against
  `p_expected_source_versions`. A member added, removed, or changed → raise `40001`
  (serialization_failure) **without writing**. The service re-reads, re-derives, and retries a
  bounded number of times. This closes the read/lock gap: two concurrent recomposes may compute
  from the same snapshot, but only the first commits.
- Applies `p_fields`, sets `source_count`, `materialized_at`, `field_provenance`.
- If `activity_date`, `sport`, or `tss` changes, records a load rebuild as pending in the same
  transaction — see "Training load rebuild".
- Writes all four athlete-owned columns from the complete live override, or clears all four to
  SQL NULL when none exists. Phase 3's provenance gate makes either state authoritative.
- An identical repeat returns current state (idempotent).

**A retry after an unseen commit resolves by re-reading, not by an idempotency key.** If the RPC
commits and the response is lost to a timeout, the client's original
`p_expected_source_versions` is now stale, so a blind resend raises `40001` and would loop until
the retry budget ran out. The rule that prevents this is narrow and testable: **the service
re-reads and re-derives before each retry**, so it sends the current versions and the RPC
applies a no-op. Combined with the rule that derived values are only rebuilt when an input
changed, the re-derived `p_fields` are identical to what was committed, and the retry converges
rather than writing a second, different result.

This is why no idempotency key is introduced. One would add a table, an expiry policy, and a
second thing to keep consistent, to reconstruct a result that re-derivation already produces.
The requirement it places on the implementation: a retry must **never** resend a cached payload,
and the retry loop must re-read inside the loop rather than above it.

**Merge policy stays in Python**, in `activity_dedup.py`. The RPC owns atomic validation and
persistence, not policy.

Two SQLSTATE facts to design around, both verified in `_postgrest_http_status`
(`api/index.py:135-181`):

- `P0002` (the house "not found" code) maps to **503, not 404**, so "activity not found" must be
  pre-checked in Python or caught locally.
- `40001` also falls to 503. Acceptable _only because_ it is retried in Python and must never
  reach a client — if it does, that is a bug, not a degraded response.

---

## Reads: one predicate

`presentation_state = 'active'` is added to **`list_activities` and `list_activities_between`
only** (`backend/repos/supabase_repo.py:496`, `:513`). Those two are the funnel for the
calendar, compliance, training load, recalibration, and the coach's `get_recent_activities`, so
one predicate collapses every view at once.

It **is** also applied to the new `list_dedup_candidates`: a superseded activity must never come
back as a merge candidate, or merges cycle.

Deliberately **not** applied to:

- `get_activity` — audit and un-merge must be able to fetch a superseded row by id.
- `list_synced_intervals_keys` — this moves to `activity_sources`, where it includes every
  non-retired Intervals identity regardless of the activity's presentation state. Supersession
  does not retire sources, so a superseded activity still blocks re-sync.

The frontend needs no schema change: `calendarActivitySchema` (`lib/schemas.ts:170`) is a
`z.looseObject`, and `components/coach-calendar.tsx` reads only `sport`, `id`, `activity_date`,
`duration_seconds`, `distance_meters`, `tss`, `avg_hr_bpm`, `rpe`, `athlete_notes`. A "merged
from N sources" affordance is a deliberate addition (Phase 6), not a correctness requirement.

---

## Bridging merges

Source C arrives and matches **both** existing activity A and activity B. One must stop being
presented — and `plan_workouts.actual_activity_id` is `on delete set null`, so deleting the
loser would **silently unlink a completed workout from the athlete's plan**.

**1. Auto-bridging is forbidden.** Tier A may attach a new source to an existing activity but
never combine two activities that each already exist. Two activities that independently
attracted sources are by construction ambiguous, and the downside is asymmetric: a missed merge
is a visible duplicate the athlete can report; a wrong merge silently destroys a plan link.
Bridging is always Tier B.

**2. Nothing is ever deleted.** The loser is marked `presentation_state = 'superseded'` with
`superseded_by_activity_id` set. The FK stays valid, reads drop it via the single predicate, the
row remains fetchable by `get_activity` for audit, and the operation is reversible.

**3. Survivor selection and plan links are explicit, under lock.**

- If exactly one side owns a plan link → that side survives, so no link moves.
- If neither owns one → earlier `created_at` survives, tie-broken by lexicographically smaller
  `id` (the same total order the reconciler uses).
- If **both own different plan workouts** → reject with `22023`. The athlete must unlink one
  first. We cannot silently discard one of two explicit assertions that these were separate
  sessions.
- Any link that does move is transferred bidirectionally inside the same transaction, after
  locking the `plan_workouts` row first and verifying `actual_activity_id` points back.
  `unlink_plan_workout_from_activity` already raises `22023` when the two sides disagree
  (`20260806003910_…:51-58`), so a one-sided link must never be propagated.

**4. Two live athlete overrides require an explicit resolution.** The one-live-override index
means blindly reparenting B when both carry a live override would raise `23505`, and any
automatic winner would silently discard athlete-authored RPE or notes. The bridge discovers
affected plan links first, acquires all locks in the sorted group order, and only then evaluates
this precondition, still **before any write**:

- At most one live override across the pair → proceed; it reparents normally.
- Two live overrides and no proposal-bound resolution → raise `22023` with both activity ids and
  change nothing.
- Two live overrides plus an explicit resolution captured in the athlete-confirmed proposal →
  retire both originals, reparent the non-override sources, and insert one replacement override
  on the survivor carrying the athlete's chosen fields. The RPC reads those fields from the
  locked proposal; the model cannot pass or alter them.

The proposal must present both override payloads side-by-side and allow the athlete to keep
either, combine them, or set explicit nulls. The merge event records both retired source ids and
the replacement id, so un-bridge retires the replacement and restores the two originals.
"Pick the newer override" and retry-after-`23505` are explicitly forbidden.

**5. The superseded activity's sources are reparented to the survivor, in the same
transaction.** This is the rule that makes bridging actually merge anything. `recompose_activity(A)`
derives from _A's_ live source set, so if B's sources kept `activity_id = B` they would
contribute nothing: the athlete would see B disappear from the calendar and A keep exactly the
numbers it already had, with B's richer metrics dropped on the floor and `source_count`
understating the group. Marking B superseded without moving its sources hides a workout instead
of merging it.

Reparenting rather than traversing `superseded_by_activity_id` at read time is deliberate.
Recompose is defined over the rows where `activity_id = <target> and retired_at is null`, and
that definition holds only if membership is a stored fact. A recompose that chased a supersession
chain would make its own input depend on the depth of that chain, and every future reader would
inherit the traversal.

**This does not weaken source immutability.** `activity_id` is membership state, not ingested
evidence — which is why the row carries both it and an `origin_activity_id` set once at insert.
The bytes, `fields`, hashes, and `raw_extraction` are untouched.

**Reversal restores membership from `origin_activity_id`.** Un-bridging clears B's
`presentation_state`/`superseded_by_activity_id`, returns the sources recorded by that bridge
event to their origin activities, applies the recorded override reversal, and recomposes both
rows. Because ordinary reparenting only moves a source _away_ from its origin and the origin is
immutable, membership restoration is exact rather than reconstructed.

### Append-only merge events

`origin_activity_id` records where a source began; it does not record which bridge moved it,
which other sources moved with it, or what happened to plan links and conflicting overrides.
Phase 5 adds an audit row written in the bridge transaction itself:

```sql
create table public.activity_merge_events (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.athlete_profiles(user_id) on delete cascade,
  event_type text not null check (event_type in ('bridge','unbridge')),
  reverses_event_id uuid,
  -- Makes the cross-row "only a bridge can be reversed" invariant FK-enforceable.
  reverses_event_type text generated always as (
    case when reverses_event_id is not null then 'bridge' end
  ) stored,
  survivor_activity_id uuid not null,
  superseded_activity_id uuid not null,
  proposal_id uuid,
  tier text not null check (tier = 'B'),
  source_transitions jsonb not null,
  plan_link_transitions jsonb not null,
  activity_transitions jsonb not null,
  created_at timestamptz not null default timezone('utc', now()),
  unique (id, user_id),
  unique (id, user_id, event_type),
  foreign key (survivor_activity_id, user_id)
    references public.activities(id, user_id),
  foreign key (superseded_activity_id, user_id)
    references public.activities(id, user_id),
  foreign key (reverses_event_id, user_id, reverses_event_type)
    references public.activity_merge_events(id, user_id, event_type),
  foreign key (proposal_id, user_id)
    references public.activity_merge_proposals(id, user_id),
  check (
    (event_type = 'bridge' and reverses_event_id is null and proposal_id is not null)
    or (event_type = 'unbridge' and reverses_event_id is not null and proposal_id is null)
  )
);

create unique index activity_merge_events_one_reversal_idx
  on public.activity_merge_events (reverses_event_id)
  where event_type = 'unbridge';

-- Response-loss recovery reads the committed bridge set by proposal.
create index activity_merge_events_proposal_idx
  on public.activity_merge_events (user_id, proposal_id)
  where proposal_id is not null;

alter table public.activity_merge_events enable row level security;
create policy activity_merge_events_owner_select
  on public.activity_merge_events for select
  using ((select auth.uid())::text = user_id);
```

The three transition arrays have versioned, closed schemas; unknown keys or a missing member
raise `22023` before insertion:

- `source_transitions`: every source changed, with `source_id`, `before_activity_id`,
  `after_activity_id`, `before_retired_at`, `after_retired_at`, and pre/post `updated_at`.
  Includes retired original overrides and any replacement override created by the bridge.
- `plan_link_transitions`: every affected `plan_workout_id` with before/after
  `actual_activity_id` and the matching activities' before/after `planned_workout_id`. Empty is
  recorded as `[]`, not omitted.
- `activity_transitions`: both activity ids with before/after `presentation_state`,
  `superseded_by_activity_id`, and `updated_at`.

The bridge RPC takes no client-authored history payload. It captures the before-state from the
rows it has locked, performs the bridge, captures the after-state, validates both sides of every
transition, and inserts the event before commit. Failure to insert rolls back the bridge. The
`proposal_id` is read from the locked consent record, never from tool arguments.

An un-bridge names the bridge event, not just two activity ids. It locks that event and the
recorded rows, requires that it has no reversal and that current state equals the recorded
after-state; later edits or another bridge produce `40001` for re-read and athlete review rather
than a destructive guess. It then applies the exact inverse, recomposes, and inserts an
`unbridge` event describing the reversal, always with `proposal_id = NULL` — consent belongs to
the referenced bridge, and copying it onto a reversal would misstate what the athlete approved.
The generated-type composite FK rejects reversing another unbridge before any state change. The
original event is never updated. Reversal is supported only while the recorded post-state is
still current; reconstructing through arbitrary later merges is out of scope.

`activity_merge_events` is retained until account deletion. `authenticated` receives `SELECT`
only through the owner policy; `INSERT`/`UPDATE`/`DELETE` are revoked from application roles.
The unique reversal index makes two concurrent un-bridges race to one success instead of applying
the inverse twice.

---

## Training load rebuild

Merging changes historical daily TSS, so `daily_load_snapshots` must be rebuilt — and **#461
means it currently cannot be**, for two independent reasons.

### Pending rebuilds live in their own table

`daily_load_snapshots` is keyed `(user_id, snapshot_date, sport)` where `sport` is nullable and
**NULL means the aggregate series** (`0001_schema.sql:280-289`), and `recompute_load_endpoint`
takes a `sport` argument. So a merge does not invalidate "load" — it invalidates _two specific
series_: the activity's sport, and the aggregate.

A single date column on an activity row cannot express that. It also cannot be cleared safely:
a `sport='cycling'` recompute would clear a marker written by a run merge, leaving the run series
permanently inflated with nothing recorded to say so. And "the earliest outstanding marker for
this athlete" would be a `min()` over their activities on an unindexed column.

So invalidation is its own table, matching how every other derived artifact in this schema is
stored:

```sql
create table public.load_rebuild_pending (
  user_id text not null references public.athlete_profiles(user_id) on delete cascade,
  sport text,                 -- null = the aggregate series, same convention as daily_load_snapshots
  pending_from date not null,
  updated_at timestamptz not null default timezone('utc', now())
);

create unique index load_rebuild_pending_key_idx
  on public.load_rebuild_pending (user_id, sport) nulls not distinct;
```

`NULLS NOT DISTINCT` (Postgres 15+) lets the aggregate row participate in the key without a
sentinel value. Ownership:

| Operation                      | Rule                                                                                                                                                                     |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| recompose / bridge / un-bridge | Upsert **two** rows — the activity's sport and the aggregate — taking `least(existing, excluded)`. May only widen a window to an earlier date, never narrow or clear it. |
| recompute                      | Deletes the row with a compare-and-swap on the `pending_from` it captured at the start of the run.                                                                       |

An unconditional clear loses work: recompute reads a marker, rebuilds for several seconds, and a
merge committing in that window writes an earlier marker that the clear then erases. Because the
marker only ever moves earlier, a failed CAS means strictly more work is outstanding than this run
did, so the correct response is to leave it pending for the next run.

### Rebuilding

1. **Seed at a date.** `get_load_snapshot_on_or_before(user_id, sport, on_date)` seeds
   `initial_ctl`/`initial_atl` from the day _before_ `since` (#461).
2. **Rebuild `[pending_from, today]`**, where `pending_from` is the earliest `activity_date` on
   either side of the transition. A Tier-B pair can span two days, so using only the post-merge
   date would leave the other day's snapshot inflated.
3. **Outside the merge transaction, and recoverable rather than atomic.** Rebuilding reads every
   activity from `pending_from` forward and rewrites a window of snapshots — far too much work to
   hold under the activity row locks. The merge writes the pending rows **inside** its
   transaction; the rebuild happens after. Recompute is idempotent, since it derives snapshots
   from the activity rows, so re-running converges. Any later merge or recompute starts from the
   earliest outstanding `pending_from` for that `(user_id, sport)`, so a dropped rebuild is
   absorbed by the next one rather than needing a dedicated retry path.
4. **Bounded by a 90-day horizon** (≈ 2 × `CTL_DAYS`). A session's residual contribution decays as
   `(41/42)^N` since `ctl += (tss - ctl) / 42` (`backend/engine/training_load.py`): at 42 days a
   duplicated 100-TSS session still moves CTL by ≈0.9, at 90 days by ≈0.27 — below what the
   athlete can perceive, and the calendar only shows 42 days of history anyway. When the rebuild
   is skipped, say so in the merge result rather than staying silent.

This is the one place the system is eventually rather than immediately consistent, and the
inconsistency is confined to a derived, recomputable table — never to the source rows.

---

## Migration and backfill

**Migration (additive, non-breaking).** Create `activity_sources` and `load_rebuild_pending`, add
the `activities` columns, create the RPCs. Existing readers keep working untouched because
`presentation_state` defaults to `'active'`.

**The cutover closes the live-write gap before backfill.** Creating the table and then batching
old rows while the old application still inserts `activities` would make the one-source invariant
false as soon as the next upload lands. Phase 1 therefore has an ordered expand/dual-write/contract
cutover:

1. The expand migration creates the tables and a temporary `AFTER INSERT` trigger for legacy direct
   activity inserts. It writes a bootstrap-grade source using the total mapping below,
   `id = activity_id`, `origin_activity_id = activity_id`, NULL hashes, and the inserted projection
   fields. The trigger exists only to cover the migration-to-application deployment window.
2. Deploy every ingestion path through a shadow `create_activity_with_source` RPC. That RPC sets a
   transaction-local marker so the temporary trigger does not also create a source, then inserts the
   activity and the same bootstrap-grade source atomically. The marker is cutover coordination, not
   an authorization boundary.
3. Run the resumable backfill for rows predating the trigger/RPC, then require the anti-join
   `activities left join activity_sources … where source.id is null` to be empty in the same release
   gate.
4. After deployment telemetry shows no legacy direct inserts, revoke direct `activities` INSERT from
   application roles and drop the temporary trigger. Phase 2 upgrades that RPC to compute hashes and
   enable exact-identity rejection; the brief Phase-1 window deliberately has backfill-grade NULL
   hashes rather than risking a missing source.

A failure at any step leaves either the legacy trigger or the RPC maintaining the invariant; never
drop the trigger merely because a deployment was requested.

**Backfill.** One `activity_sources` row per existing `activities` row. The source map is total over
the current `activities_source_check` allow-list and is the only mapping the backfill may use:

| existing `activities.source` | `provider`  | `ingest_format` |
| ---------------------------- | ----------- | --------------- |
| `manual`                     | `athlete`   | `manual`        |
| `text_extract`               | `athlete`   | `text`          |
| `gpx_upload`                 | `unknown`   | `gpx`           |
| `fit_upload`                 | `unknown`   | `fit`           |
| `tcx_upload`                 | `unknown`   | `tcx`           |
| `screenshot_extract`         | `unknown`   | `screenshot`    |
| `intervals_sync`             | `intervals` | `intervals_api` |

`file_upload` is deliberately **not** mapped — it is the invalid application fallback in #462, not a
permitted stored source. Before writing any source rows, a preflight groups all historical activities
by `source` and requires the set to be a subset of the seven rows above; any other value, including a
constraint-bypassed `file_upload`, aborts the whole backfill and must be repaired explicitly. The same
preflight requires every `intervals_sync` key to parse as `intervals:{id}` with parsed ids unique per
user. Do not use a default branch — a newly permitted source without a reviewed mapping must fail
before the first batch, not silently become `unknown`.

Then populate:

- `origin_activity_id` = `activity_id` = the existing row's id
- `object_key` ← `source_file_key`; `external_id` ← the id inside `intervals:{id}`
- `fields` ← the row's own metric columns
- `payload_fingerprint` computed from stored fields only when the information gate passes
  (`started_at` present and at least one of `duration_seconds` or `distance_meters` positive).
  Sparse rows get NULL, exactly as new ingests do
- `content_hash` **NULL** — the original bytes are in R2, but re-downloading and hashing every
  historical file is disproportionate. Consequence: a historical file re-uploaded after cutover is not
  rejected as an exact duplicate; it is stored and reaches the athlete through the tiered merge instead

**Batches are restart-safe, not merely small.** A temporary `activity_sources_backfill_progress` row
stores the last committed `(created_at, id)` keyset position. Each batch transaction selects its
activity rows `FOR UPDATE` in that order, derives fields only after those locks, inserts bootstrap
sources with deterministic `activity_sources.id = activities.id`, and advances the checkpoint only
after those inserts commit. A crash rolls back both; a retry resumes at the prior key. An
implementation that precomputes outside the lock must record the expected `updated_at` and retry on
any change after locking.

When `ON CONFLICT (id) DO NOTHING` inserts nothing, the transaction compares the existing row's
`user_id`, activity ids, format/provider, fields, and NULL hashes before advancing — a mismatch
aborts rather than being mistaken for completed work. The temporary trigger covers concurrent rows
beyond the checkpoint.

A legacy update racing a batch has only two outcomes: it commits first and its values are captured,
or it waits for the batch and becomes a later, versioned projection change. Phase 3 scans for that
drift and converts the difference — athlete fields and date corrections included — into explicit
override provenance. It is never folded into the already-captured source.

After the keyset reaches the end, run the anti-join and the exactly-one-source invariant without a
limit. Any hole resets the checkpoint to the earliest missing row and reruns; only a clean second
pass marks completion. The contract migration may drop the progress table and legacy trigger only
once that marker and the direct-INSERT revoke are both present.

**The backfill cannot collide**, because none of its three identity fields can conflict:
`payload_fingerprint` has no unique index, `content_hash` is uniformly NULL, and the only backfilled
external ids are parsed from `intervals:{id}`, which the existing constraint has already made unique
per athlete (preflight asserts this rather than trusting it). So every historical activity backfills
as an active, unmerged row with exactly one source, and no legacy row is dropped or namespaced.

Run the batches independently in preview and production; the projects have separate progress rows and
must each be linked, verified, and contracted.

**Historic duplicates are not retro-merged.** They are surfaced by the opt-in backlog pass (Phase 5)
and confirmed by the athlete. Silently restating someone's training history at deploy time is worse
than leaving it and asking.

Per AGENTS.md, `docs/supabase-migration-history.md` is updated **in the same change** — both the
"Canonical migration sequence" bullet and a section with
`**File:** / **Change:** / **Why (issue #N):** / **Security note:** / **All environments:**`.

---

## Phases

Each phase is independently shippable and ends in something verifiable.

**Phase 0 — unblock.** Ship #461 and #462 as independent fixes, ideally merged before Phase 1
rather than tracked inside it (see "Sequencing"). _Verifiable:_ a backward window rebuild produces
correct CTL; a rebuild window holding more than 500 activities still includes the oldest of them; a
`.fit` file with no suffix saves instead of 503-ing.

**Phase 1 — schema and gap-free shadow write.** Expand migration, temporary legacy-insert trigger,
shadow `create_activity_with_source` RPC on every ingestion path, resumable backfill, invariant
validation, then revoke direct inserts and remove the trigger in that order. Add `Activity` model
fields and repo methods. The shadow source uses NULL identity hashes, so presentation behaviour does
not change. _Verifiable:_ `bun run db:reset` replays clean; writes during every cutover step get
exactly one source; killing a batch before and after its checkpoint update converges without
duplicate/missing rows; an interrupted deployment leaves the trigger active; preflight rejects an
unmapped source or malformed Intervals identity; **every activity has exactly one non-retired source
whose mapping and fields round-trip at its captured activity version**; sparse historical rows have a
NULL fingerprint.

**Phase 2 — identity-aware write path.** Upgrade the Phase-1 RPC to hash and fingerprint every new
input. Select-then-insert exact-duplicate 409 on authoritative identifiers only. **Convert Intervals
sync to the canonical ingestion entry point** — both the source-backed
`list_synced_intervals_keys` read and the `create_intervals_activity` upsert, whose `on_conflict`
target is the constraint being dropped — then drop the legacy activity uniqueness constraint and
generated column before Phase 3. Still no merging. _Verifiable:_ re-uploading a file returns 409
naming the existing activity; a repeated known-provider external id is race-safe and returns the same
result; the same id under `provider = 'unknown'` does not reject; re-uploading a ZIP rejects every
member; two distinct ZIP members both save; **the same ride uploaded as both `.fit` and `.gpx` stores
two sources rather than rejecting the second**; **two distinct sessions colliding on
`payload_fingerprint` are both stored**; and **Intervals sync remains idempotent across the constraint
drop**, with a re-sync of an already-synced ride creating no second activity.

**Phase 3 — athlete overrides (must precede any recompose).** Convert `repo.update_activity`,
`merge_activity_text_update`, **and `build_activity_from_text`** to write an `athlete_override` source
and route through recompose. Before enabling that path, backfill every existing non-null athlete field
into a complete four-key override, convert any post-backfill projection drift (including corrected
dates) into override provenance, assert that every row without one has four NULL athlete columns, then
revoke direct `activities` UPDATE.

The third writer is easy to miss and is not optional: it populates `rpe`, `athlete_notes`, and
`fueling_notes` directly on a `text_extract` activity today
(`backend/services/activity_text.py:667-669`), so an athlete who describes a session in chat gets
athlete-authored values on a source whose `ingest_format` is `text`, not `athlete_override`. Left
unconverted, the authoritative no-override rule would clear those values on first recompose.
_Verifiable:_ an athlete note and a corrected date both survive a subsequent recompose, **including a
note that originated from a chat text extract rather than an explicit edit**.

**Phase 4 — materialization.** `activity_dedup.py` scorer + reconciler, `recompose_activity` RPC,
Tier-A auto-merge wired into `_finalize_persisted_activity` **before** `_try_match_activity_to_plan`,
and the `presentation_state` read predicate. The ordering matters: reversing it lets a duplicate steal
the planned workout from the surviving activity. _Verifiable:_ a FIT upload of an already-synced
Intervals ride yields one calendar entry carrying the FIT's richer measurements and the Intervals TSS;
compliance stops reporting the phantom unplanned session.

**Phase 5 — Tier B + bridging.** The proposal/member schema, the shared normalization function, and
the atomic server-side consent protocol; append-only merge-event schema and transactional
bridge/un-bridge audit; coach tools (`find_duplicate_activities`, `merge_activities`,
`unmerge_activity`) in `lib/agent/tools.ts` routed via `postEngine` in `lib/agent/coach-tools.ts`;
system prompt guidance; bridging rules. _Verifiable:_ the coach can surface the backlog and merge only
after explicit athlete confirmation; a both-sides-linked bridge is refused; a bridge with two live
overrides makes no writes without a proposal-bound resolution, and a confirmed resolution survives
merge and reverses exactly on un-bridge.

**Phase 6 — UI.** "Merged from N sources" affordance in `components/coach-calendar.tsx`. Must survive
the narrow-viewport dot mode (`coach-calendar.module.css`, `.chipLabel { display: none }`) and the
`MAX_CHIPS_PER_DAY = 3` overflow.

---

## Risks, assumptions, and accepted gaps

**Assumptions:**

1. **Provider is mostly `unknown` at first, and no provider-dependent destructive decision is made
   for it.** `activities` has no provider column today; external identity is smuggled into
   `source_file_key` as `intervals:{id}`. Uploads can sometimes infer a provider from file internals
   (FIT `manufacturer`) but will often be `unknown`. This is safe because unknown-provider rows are
   excluded from the external-id key and can reject only on `content_hash`, whose byte identity is
   independent of the label.
2. **The Garmin sidecar writes nothing today** (local files only, #388). When server upload lands, the
   Garmin activity id becomes a real `external_id` and the strongest dedup key available. This design
   should not be finalized without checking it composes with #388.
3. **`started_at` is trustworthy across providers.** Tier A's ±10 min window assumes providers agree on
   the start instant to within minutes. Timezone or clock-skew bugs would make Tier A either over- or
   under-merge.
4. **Sport `"general"` is common enough to matter.** Treating it as _undeclared_ rather than as a value
   is a real semantic choice; if most uploads land there, most pairs become groupable on weaker
   evidence.

**Risks, in rough order:**

1. **Grouping, not dedup, is the fuzzy part.** Exact dedup is two identity indexes and is essentially
   free. Deciding that two _different_ records describe one workout is heuristic, and every tolerance
   in the Tier A/B table is a guess until it meets real athlete data. Budget for tuning.
2. **Bridging destroys plan links if done carelessly.** Addressed by forbidding auto-bridging and
   refusing the both-linked case, but it remains the most dangerous operation here — and the
   bidirectional link storage described at the top is why.
3. **The load rebuild is the only eventually-consistent seam**, and it depends on #461, whose absence is
   invisible until you specifically test a backward window.
4. **The coach gets exactly one tool call per turn** (`lib/agent/coach-tools.ts:382`,
   `isEnabled: !runContext.context.toolCalled`). A dedup conversation that needs _find → present →
   merge_ therefore spans multiple turns by construction. The Tier-B flow must be designed around that.
5. **Backend/frontend activity shapes drift silently.** `calendarActivitySchema` is a `z.looseObject`
   and the backend dumps the full Pydantic `Activity`, so new fields cross the wire unvalidated. Adding
   `source_count` will "just work", which is exactly the hazard.
6. **Recompose cost grows with source count.** Fine at N ≤ 5; if a provider ever fans out many sources
   per activity this needs a bound.
7. **`content_hash` is unbackfillable in practice**, so the first months after cutover have weaker
   exact-dedup on historical files than on new ones. Accepted rather than worked around: the
   alternative — letting `payload_fingerprint` reject in its place — trades a recoverable duplicate for
   unrecoverable data loss.

**Accepted gaps:**

- **A Tier-A merge does not change anything the coach already said.** Chat turns are persisted as
  written (`chat_messages.parts`), and nothing rewrites history. So if the coach discussed an activity
  and a later upload merges into it, yesterday's message still quotes yesterday's numbers while the
  calendar shows the merged ones. This is the correct behaviour for a chat transcript — an edited
  transcript would be worse — but it does mean an athlete can see two different figures for one ride.
  No mitigation in this design; revisit if it is reported.
- **Historical TSS may be recomputed under current thresholds** until #463 lands. Bounded as described
  under "Derived values".
- **Provider TSS is preferred without a confidence model** until #464 lands.

---

## Open decisions

1. **Tier A/B tolerances.** ±10 min, ±5%, ±1 day, ±10% are guesses until they meet real athlete
   data. Budget for tuning after Phase 4 rather than trying to get them right up front.

**Settled since the first draft**, recorded so they are not reopened:

- **Source fidelity is a reference table**, not a generated column — see "Source fidelity lives in
  a reference table".
- **Every environment is on Postgres 17**: local `supabase/config.toml:36`, preview 17.6.1.105,
  production 17.6.1.155 (`supabase projects list`). So `NULLS NOT DISTINCT` on
  `load_rebuild_pending` and any other 15+ feature are safe to rely on.

---

## Files this design will touch

| Area      | Files                                                                                                                                                                                                                                                                                                                                                                                                                         |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Migration | `supabase/migrations/<ts>_activity_ingest_formats.sql`, `<ts>_activity_sources.sql`, `<ts>_load_rebuild_pending.sql`, `<ts>_retire_intervals_activity_key.sql`, `<ts>_recompose_activity_rpc.sql`, `<ts>_activity_merge_consent.sql`, `<ts>_activity_merge_events.sql`, `docs/supabase-migration-history.md`                                                                                                                  |
| Model     | `backend/models/training.py` (`Activity` gains the new columns; new `ActivitySource`)                                                                                                                                                                                                                                                                                                                                         |
| Repo      | `backend/repos/supabase_repo.py` — `presentation_state` predicate on `list_activities`/`list_activities_between`; move `list_synced_intervals_keys` to source identities; **replace `create_intervals_activity`'s constraint-targeted upsert**; new `create_activity_with_source`, `list_activity_sources`, `list_dedup_candidates`, `recompose_activity`, `get_load_snapshot_on_or_before`, `load_rebuild_pending` accessors |
| Services  | `backend/services/activity_dedup.py` (new — pure scorer + reconciler); `backend/services/activity_text.py` (Phase 3); `backend/services/intervals.py` (Phase 2)                                                                                                                                                                                                                                                               |
| API       | `api/index.py` — `_finalize_persisted_activity` (:2448), `_persist_extracted_activity` (:2430), `_build_uploaded_activity_or_course` (:1551), `_zip_activity_entry` (:1779), `intervals_sync` (:557), `recompute_load_endpoint` (:1439), `_activity_source_for_filename` (:1513), new find/merge/unmerge endpoints                                                                                                            |
| Agent     | `lib/agent/tools.ts`, `lib/agent/coach-tools.ts`, `lib/agent/system-prompt.ts`                                                                                                                                                                                                                                                                                                                                                |
| Frontend  | `components/coach-calendar.tsx`, `lib/schemas.ts` (Phase 6)                                                                                                                                                                                                                                                                                                                                                                   |

**Reuse rather than rebuild:** the pure-scorer shape of `match_activities_to_workouts`
(`compliance.py:85`); the `unlink_plan_workout_from_activity` RPC template (`20260806003910_…`); the
conditional-unique idiom from `20260716000000_intervals_sync_idempotency.sql`;
`build_activity_summary_from_fields` (`supabase_repo.py:255`); `recompute_load_series`
(`backend/engine/training_load.py`).

---

## Verification

**Per phase, run `bun run check` and `uv run pytest`.** Full gate before handoff: `bun run lint`,
`bun run typecheck`, `uv run ruff check .`, `uv run ty check`, `uv run vulture`,
`bun run ast-grep:check`. Pre-push runs all of these plus Playwright — do **not** skip UI tests for
Phase 6.

**Database.** `bun run db:reset` to replay migrations locally, then `bun run test:db` for the
`@pytest.mark.db` guard-clause tests (excluded from the default run). Before remote apply:
`supabase migration list --linked` and `supabase db push --linked --dry-run`, against preview and
production separately.

**New tests:**

| File                                                                                    | Covers                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `tests/python/test_activity_dedup.py` (new)                                             | Pure scorer: Tier A/B boundaries, null/zero rules at both tiers, hard negatives, sport conflict, `general`. Reconciler: fidelity/tie order, exact four-key override including explicit nulls, all athlete columns cleared with no override, derived values rebuilt only when an input changed, provider TSS preferred over recomputation, order independence. Regression: bridge a B-only override onto A, then un-bridge                                                                                          |
| `tests/python/test_supabase_db.py` (schema invariants)                                  | The ingest-format FK rejects an unknown format; `fidelity_rank` is unique so the merge order is total; application roles cannot write `activity_ingest_formats`. Backfill mapping; gap and shadow inserts. Backfill checkpoint failures, repeat, conflicts, and holes prove restart behavior. Race a legacy UPDATE on both sides of the row lock. Trigger removal requires clean second pass plus INSERT revoke. Override uniqueness. `presentation_state` agrees with the live-source-set predicate for every row |
| `tests/python/test_supabase_db.py` (ownership guards)                                   | Source evidence immutability and projection write privileges/RPC column sets. Identity fields immutable through definer RPCs. Provenance winner retirement/restoration is deterministic. No non-override source raises `22023`                                                                                                                                                                                                                                                                                     |
| `tests/python/test_supabase_db.py` (load invalidation)                                  | Recompose/bridge/un-bridge upsert both the sport row and the aggregate row and may only widen `pending_from`. Recompute deletes only by compare-and-swap. A concurrent earlier invalidation survives a slow rebuild. A cycling recompute cannot clear a running row                                                                                                                                                                                                                                                |
| `tests/python/test_supabase_repo.py`                                                    | Extend fakes for sources/recompose; unknown RPC fails, calls are exact, composite-row data is a dict. Presentation filtering stays on activity lists, not `get_activity`. `list_synced_intervals_keys` reads non-retired Intervals source identities rather than the projection                                                                                                                                                                                                                                    |
| `tests/python/test_supabase_db.py` (RPC invariants)                                     | Cross-user/version/retry and sorted group locking; override preconditions follow locks. Identity indexes/events are atomic. Un-bridge rejects stale state, a non-null proposal, and reversal of an unbridge before writes, then restores exactly. Only one reversal can win. Authenticated RLS can read the owner's history but not another athlete's                                                                                                                                                              |
| `tests/python/test_api.py`                                                              | Exact-identity/fingerprint and Tier-A-before-plan-link cases. Tier B rejects wrong-user, expired, consumed, stale-version, substituted-member, pre-proposal, assistant-authored, and non-matching confirmations without a write; a missing user-turn row rejects rather than falling back; the model tool schema cannot supply a message id or resolved override. A valid group confirmation merges atomically and consumes once                                                                                   |
| `tests/python/test_calendar_api.py`, `test_compliance_api.py`, `test_intervals_sync.py` | Superseded rows leave calendar/compliance. Intervals idempotency survives the constraint drop and reads live source ids; a re-sync after the canonical-entry-point conversion creates no second activity; a bridge can project a reparented Intervals source without a uniqueness failure                                                                                                                                                                                                                          |
| `tests/python/test_engine.py`                                                           | Seed-at-date rebuild correctness; rebuild starts at the earliest affected date; the 90-day horizon; a dropped rebuild leaves its pending rows and is absorbed by the next run                                                                                                                                                                                                                                                                                                                                      |
| `tests/web/agent-tools.test.ts`                                                         | The three new tool schemas; merge accepts a proposal id but exposes no confirmation message id, confirmation boolean/text, member replacement, or override-resolution argument (nested object fields remain `.nullable()`, not `.optional()`)                                                                                                                                                                                                                                                                      |
| `tests/ui/calendar.spec.ts`                                                             | The merged-sources affordance, including narrow-viewport dot mode                                                                                                                                                                                                                                                                                                                                                                                                                                                  |

**End-to-end manual check** (`bun run dev:local`): upload a FIT file → confirm one calendar entry;
upload the identical file again → confirm 409 naming the first activity; sync the same ride from
Intervals.icu → confirm still **one** calendar entry carrying the FIT's richer measurements and
`source_count = 2`; add an RPE, re-sync, confirm the RPE survived; ask the coach for a compliance
summary and confirm no phantom unplanned session.
