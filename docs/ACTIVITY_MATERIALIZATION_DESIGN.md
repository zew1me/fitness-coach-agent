# Activity Materialization & Deduplication

**Status: proposed, not implemented.** Every reference to existing code in this
document is real and current; every reference to new tables, columns, RPCs, and
services is a plan. Nothing described here has been built yet.

## Context

Today `activities` rows are shown to the athlete exactly as ingested. One real-world
workout that arrives twice — a FIT upload plus the Intervals.icu sync of the same
ride, a ZIP re-import, a re-uploaded file — becomes two rows, and both are presented
as two separate workouts.

That is not cosmetic. It corrupts two athlete-facing numbers:

- **Training load double-counts.** `recompute_load_endpoint` (`api/index.py:1439`)
  sums `tss` per `activity_date`. Because CTL is an exponentially-weighted average
  with `CTL_DAYS = 42` (`backend/engine/training_load.py`), one duplicated ride keeps
  inflating fitness for weeks.
- **Compliance invents a workout.** `match_activities_to_workouts`
  (`backend/services/compliance.py:85`) is a strict 1:1 assignment, so only one copy
  binds to the planned workout. The other keeps `planned_workout_id is None` and is
  reported as an _unplanned_ session (`compliance.py:229`). The coach then
  congratulates the athlete on a session they never did.

There is exactly one dedup mechanism today and it covers one source:
`intervals_source_file_key`, a generated column that is `NULL` for every non-Intervals
row (`20260716000000_intervals_sync_idempotency.sql`). Uploads, ZIP members, and text
extracts have **zero** duplicate protection.

**Intended outcome.** Each ingested input is stored immutably and separately in a new
`activity_sources` table. The `activities` row becomes a _derived projection_ — the
materialized view the athlete, the calendar, and the coach all read. An exact
duplicate from the same provenance is rejected outright; anything else is stored and
merged into one presented activity.

### Scope note

A competing model — a mutable in-place survivor — was proposed in PR #450
(`design-doc-dedupe`). **That PR is now closed and this document is the design of
record for #397.**

This design was developed **independently** of it, and where the two reached similar
conclusions on architecture-independent questions — tier thresholds, null and zero
degradation, the load-rebuild boundary — that was convergence rather than derivation.
Five further conclusions were **subsequently adopted** from it after comparison, and
are listed under "Ported from the in-place design (PR #450)" so the inheritance is
explicit rather than implied. Verified blocker C below also originates there.

---

## Architectural decisions (settled — do not relitigate during execution)

**1. Not a Postgres `MATERIALIZED VIEW`.** Two disqualifiers: RLS cannot be applied to
a matview, and `20260816191358_rls_and_security.sql` just enabled RLS on every table —
a matview would be a security regression on the most sensitive table in the schema.
Matviews also only support full `REFRESH`, never incremental maintenance, so one
athlete's upload would rebuild the whole table. "Materialized view" here means a
**RPC-maintained summary table**, which is also the established house pattern: this
schema has zero views and zero triggers beyond `set_updated_at`, and every derived
artifact (`daily_load_snapshots`, `activity_summary`, `generation_context`) is
computed in Python and written explicitly.

**2. `activities` keeps its `id` and stays the row everything reads.** It becomes
derived; `activity_sources` is a new child table with `activity_id → activities.id`.

Rejected alternative: a new `activity_groups` table with readers repointed at it. It
delivers the identical user-visible outcome for a far larger blast radius —
`plan_workouts.actual_activity_id` FKs to `activities(id)`, and every index, reader,
and the three existing plan-link RPCs would have to be rewritten.

**3. Dedup uses two kinds of key, and only one kind may reject.** They are not two
flavours of the same mechanism, and the asymmetry between them is a requirement of
this design rather than an implementation detail.

- **Authoritative identifiers — `content_hash` (sha256 of the raw bytes) and a
  provider `external_id`.** These assert _identity_: this input **is** an input
  already stored. They may reject an ingestion outright, and they are enforced by a
  unique index.
- **Heuristic evidence — `payload_fingerprint` (a hash over normalized extracted
  fields).** This asserts _similarity_: two inputs look like the same recording. That
  is a judgement, not an identity. It narrows the candidate set for the tiered merge
  and **never rejects anything**. It carries no unique index.

The reason is the cost asymmetry. A false negative on dedup leaves a visible duplicate
the athlete can report and the coach can merge. A false positive on _rejection_
destroys a workout the athlete actually did — silently, at the moment of upload, with
nothing stored to un-merge afterwards. Fuzzy similarity therefore never gets to be a
uniqueness constraint. This is spelled out in "`payload_fingerprint` is candidate
evidence, never a constraint" below.

`content_hash` is scoped `(user_id, content_hash)` — byte identity and nothing else,
for the reasons in the scoping section below. That is what makes a Garmin FIT and the
Intervals record of the same ride **merge** (different bytes) while a byte-identical
re-upload of the same file **rejects**.

**4. Exact duplicate → `409`, naming the existing activity.** "Exact" means an
authoritative identifier matched — never a fingerprint match. The repo's central
`PostgRESTAPIError` handler already maps SQLSTATE `23505` → 409 with a documented
rationale (`api/index.py:135-181`), so the reject path is nearly free.

**5. Two merge tiers.** Tier A auto-merges high-confidence matches; Tier B proposes to
the athlete and requires confirmation.

---

## Verified blockers found during exploration

All three confirmed by reading source; all three must be fixed as part of this work.

**A. A backward load rebuild is structurally impossible today.**
`recompute_load_endpoint` seeds `initial_ctl`/`initial_atl` from
`repo.get_latest_load`, which is `order("snapshot_date", desc=True).limit(1)`
(`backend/repos/supabase_repo.py:574-584`) — the **newest**, already-inflated
snapshot, regardless of the `since` argument. Merging two activities changes
historical daily TSS, so this must be fixed with a `get_load_snapshot_on_or_before`
seed-at-date lookup before any merge can correct load.

**B. `_activity_source_for_filename` can emit an invalid `source`.**
`api/index.py:1513` falls back to `"file_upload"`, which is **not** in
`activities_source_check`. Reachable because `_parse_uploaded_activity_file` (`:1524`)
dispatches on _content_type OR suffix_ while the source is derived from _suffix
alone_ — so a file named `ride` sent as `application/vnd.garmin.fit` parses fine and
then fails at insert with a 503. Fix while touching this code path.

**C. The load rebuild reads through a display-capped query.**
`recompute_load_endpoint` loads its candidates with
`repo.list_activities(user_id, sport=…, since=since, limit=500)`
(`api/index.py:1448`), and `list_activities` applies
`.order("activity_date", desc=True).limit(limit)`
(`backend/repos/supabase_repo.py:496-511`). Ordering newest-first and capping means
any rebuild window holding more than 500 activities silently drops its **oldest**
rows from `daily_tss` — precisely the rows a backward rebuild is walking forward
from — and rebuilds every snapshot in the window on incomplete input. Recompute needs
a dedicated unbounded, keyset-paginated query rather than a display-oriented one. This
defect is independent of deduplication and was surfaced by the in-place design in
PR #450; it is listed here because merging cannot correct load without it.

---

## Data model

### New table: `activity_sources`

One row per ingested input, whose evidence fields are immutable and whose narrow
lifecycle fields are not — the split is enumerated and enforced in "The evidence/state
boundary" below. Migration file
`supabase/migrations/<ts>_activity_sources.sql`, all-lowercase, prose header naming
the issue — matching the house style in `20260708000000_intervals_connections.sql`.

```sql
create table public.activity_sources (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references public.athlete_profiles(user_id) on delete cascade,

  -- Composite FKs, not plain id references: they make "this source belongs to the
  -- same athlete as its activity" a database invariant rather than an RPC habit.
  -- Requires `alter table public.activities add constraint activities_id_user_id_key
  -- unique (id, user_id);` (redundant to the PK, and that is the point — it is what
  -- a composite FK can target). See "Cross-user references are closed by the schema".
  activity_id uuid not null,
  origin_activity_id uuid not null,
  foreign key (activity_id, user_id)
    references public.activities(id, user_id) on delete cascade,
  foreign key (origin_activity_id, user_id)
    references public.activities(id, user_id) on delete cascade,

  provider text not null check (provider in (
    'garmin','intervals','strava','wahoo','coros','polar','suunto','athlete','unknown'
  )),
  ingest_format text not null check (ingest_format in (
    'fit','gpx','tcx','intervals_api','text','screenshot','manual','athlete_override'
  )),
  external_id text,
  object_key text,
  content_hash text,
  payload_fingerprint text,
  fields jsonb not null default '{}'::jsonb,
  raw_extraction jsonb,
  -- Generated, never supplied: the rank and the format cannot disagree if only one
  -- of them is ever written. See "fidelity_rank is derived, not stored alongside".
  fidelity_rank integer generated always as (case ingest_format
    when 'athlete_override' then 0
    when 'fit' then 1
    when 'tcx' then 2
    when 'gpx' then 3
    when 'intervals_api' then 4
    when 'text' then 5
    when 'screenshot' then 6
    when 'manual' then 7
  end) stored,
  recorded_at timestamptz,
  retired_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

-- Byte identity only. Deliberately not scoped by provider or ingest_format: both are
-- inferred labels, and no inferred field belongs in the one key that can reject an
-- athlete's upload. See "Scoping the one key that can reject".
create unique index activity_sources_content_hash_idx
  on public.activity_sources (user_id, content_hash)
  where content_hash is not null and retired_at is null;

-- Deliberately NOT unique: payload_fingerprint is candidate-generation evidence,
-- never a rejection key. A collision must narrow the merge search, never discard an
-- activity. See "payload_fingerprint is candidate evidence, never a constraint".
create index activity_sources_fingerprint_idx
  on public.activity_sources (user_id, payload_fingerprint)
  where payload_fingerprint is not null and retired_at is null;

create index activity_sources_activity_idx
  on public.activity_sources (activity_id) where retired_at is null;

-- At most one live athlete override per activity. Retiring the prior override and
-- inserting the new one is the writer's job; this index is what makes a writer that
-- forgets fail loudly instead of silently resurrecting an older edit.
create unique index activity_sources_one_live_override_idx
  on public.activity_sources (activity_id)
  where ingest_format = 'athlete_override' and retired_at is null;

create trigger activity_sources_set_updated_at
before update on public.activity_sources
for each row execute function public.set_updated_at();
```

The same treatment applies to `activities.superseded_by_activity_id`, which otherwise
lets one athlete's activity be superseded by another's:

```sql
alter table public.activities
  add constraint activities_superseded_by_same_user
  foreign key (superseded_by_activity_id, user_id)
  references public.activities(id, user_id) on delete set null;
```

### `fidelity_rank` is derived, not stored alongside `ingest_format`

A plain `integer not null` accepts `('fit', 7)` — a FIT source ranked below a manual
entry. Nothing would reject it, and the failure is silent in the worst way: the
reconciler sorts by rank, so the manual entry's numbers overwrite the FIT's on a
merged activity, and the athlete sees a ride with wrong power and no obvious cause.
One writer inserting a stale constant is enough to cause it.

Making the column `generated always as (…) stored` removes the possibility rather than
policing it — the rank is a pure function of `ingest_format`, and now Postgres computes
it. The mapping is total over the eight permitted formats, so there is no `else`
branch to go stale, and adding a ninth format fails loudly at migration time with a
`null` rank rather than quietly sorting it first.

It composes with the evidence guard below: a generated column can only change when
`ingest_format` changes, and `ingest_format` is immutable evidence, so `fidelity_rank`
inherits that immutability without being named in the guard. Keeping it stored rather
than virtual keeps it indexable and keeps `to_jsonb(new)` comparisons in the guard
cheap.

### Cross-user references are closed by the schema, not by the RPCs

`user_id` and `activity_id` constrained independently permit a row that names one
athlete and points at another athlete's activity. RLS does not close this: the owner
policy tests `user_id` alone, so a mislabelled row is perfectly visible to the athlete
it names while carrying someone else's data. Neither does the service-role write path
— every RPC takes `p_user_id` from `require_user_context()` and never from a request
body, but that is a property of code that must be re-established in each new RPC, and
this design adds several.

Composite foreign keys make it structural instead. `(activity_id, user_id)` can only
resolve to a row of `activities` that carries the same `user_id`, so a cross-user
reference is not rejected at review time or at request time — it is unrepresentable.
The redundant `unique (id, user_id)` on `activities` exists solely to give those FKs a
target.

This is the same principle as the evidence guard below: where an invariant can be
stated to Postgres, state it there. RPC-level owner checks remain (a caller must still
be denied the _existence_ of another athlete's activity), but they are no longer the
only thing standing between a bug and a tenancy leak.

Plus `enable row level security` with the owner policy
(`(select auth.uid())::text = user_id`), and the standard
`revoke all … / grant … to service_role` block.

**The one unique index is a partial _index_, not a constraint** — deliberately. It
excludes retired rows, which a constraint cannot. Nothing upserts against it (we
insert and let `23505` propagate), so the PostgREST limitation that `on_conflict`
cannot name a partial index does not apply here. The fingerprint index alongside it is
an ordinary lookup index and enforces nothing.

Three cases these keys must get right:

| Case                            | Behaviour               | Why                                                                                                                                                                                      |
| ------------------------------- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Re-upload the same ZIP          | every member rejected   | Members have distinct bytes → distinct `content_hash`; the archive's shared `object_key` is no longer a dedup key, so the constraint that `20260716000000` had to work around disappears |
| Two distinct members of one ZIP | both stored             | Distinct bytes, distinct hashes                                                                                                                                                          |
| Text extract                    | never rejected as exact | No bytes ⇒ `content_hash` NULL, and NULLs are distinct in a unique index. A fingerprint may still be computed — it just cannot reject                                                    |

### Scoping the one key that can reject: `(user_id, content_hash)` and nothing else

There is exactly one unique key in this design, and it is scoped
**`(user_id, content_hash)`**. Neither `provider` nor `ingest_format` appears in it,
and both omissions are deliberate.

**Why not `ingest_format`.** Identical bytes cannot be two different recordings. If
the same file arrives once named `ride.fit` and once named `ride.gpx`, the athlete has
uploaded one file twice — rejecting the second is the correct answer, and scoping by
format would turn that into two stored copies of one recording. This does not touch
the genuine same-ride-two-formats case: a Garmin export produced as `.fit` and as
`.gpx` contains **different bytes**, so it has a different hash, is never rejected, and
is stored as two sources that merge on their fields. Byte identity and format are
independent questions, and only byte identity is being asserted here.

**Why not `provider`.** There is no `provider` column anywhere in the codebase today;
every upload path derives only a _format_ from the filename suffix
(`_activity_source_for_filename`, `api/index.py:1513`), so on day one of Phase 2
essentially every upload lands in `provider = 'unknown'`. Including provider would
mean the same bytes re-uploaded under a later, better-inferred label no longer collide
with the earlier row and are silently stored — an inferred label quietly weakening a
key whose entire job is byte identity. The same user's identical bytes are the same
recording regardless of what the pipeline guessed about their origin, so provider adds
nothing and can only cost recall.

**The rule underneath both.** Narrowing a rejection key produces a missed duplicate;
widening one produces a refused workout. `(user_id, content_hash)` is the widest scope
that is still an identity claim, and **no field that is inferred rather than derived
from the bytes may enter it**. That is what keeps `provider = 'unknown'` from being a
correctness problem: the labels this pipeline guesses at are exactly the labels the key
excludes.

### `payload_fingerprint` is candidate evidence, never a constraint

**Requirement.** `payload_fingerprint` may narrow the candidate set for the tiered
merge below. It may **not** reject an ingestion, and it carries no unique index. Only
authoritative identifiers — `content_hash`, or a provider `external_id` once one is
available — may reject an ingestion outright. A fingerprint collision must never
silently discard a legitimate activity.

This is not a tuning preference that a later, cleverer normalization could overturn.
The fingerprint hashes rounded, provider-disagreeing values by construction: distance
is rounded to 10 m, `started_at` truncated to the second, and elevation excluded
entirely (below). Rounding is what makes it useful for _matching_ and is exactly what
makes it unsound for _identity_ — two genuinely different sessions that happen to
round together are indistinguishable from one session recorded twice. Endurance
training supplies those collisions readily: an athlete repeating a fixed 60-minute
trainer session at the same time on consecutive days, or two loops of the same
circuit, produce near-identical normalized fields.

Both outcomes are wrong; they are not equally wrong:

| Failure                                  | Cost                                                                                    | Recoverable?                                                 |
| ---------------------------------------- | --------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| Fingerprint misses a real duplicate      | A visible duplicate the athlete can see and the coach can merge                         | Yes — Tier B exists for exactly this                         |
| Fingerprint rejects a legitimate session | A workout the athlete did is never stored; load and compliance are quietly short by one | **No.** Nothing was written, so there is nothing to un-merge |

A unique index converts the second row into data loss at the moment of upload, before
the athlete has any surface on which to disagree. Everything downstream in this design
— the merge tiers, the athlete confirmation, the un-merge path — exists because
similarity judgements need to be reversible. Putting one of those judgements behind a
constraint puts it outside that machinery entirely.

**What the fingerprint is for instead.** It is an index-backed prefilter for
`list_dedup_candidates`: an exact fingerprint match is strong evidence and promotes a
pair for scoring, but the pair still passes through the Tier A/B predicate, the hard
negatives, and — for Tier B — athlete confirmation. Nothing merges because two
fingerprints are equal; something merges because the scorer said so on the underlying
fields.

Because it enforces nothing, its scoping is a recall question rather than a safety
one, and it is scoped `(user_id, payload_fingerprint)` — deliberately _not_ by
`provider` or `ingest_format`, since matching a Garmin FIT to the Intervals record of
the same ride is precisely the cross-provenance case this design exists to catch.

### What the fingerprint hashes, and why a start instant is required

The fingerprint hashes normalized extracted fields: `sport`, `started_at` truncated to
the second, `duration_seconds`, and `distance_meters` rounded to 10 m.

**`elevation_gain_meters` is deliberately excluded.** Garmin's barometric elevation
and a GPX's DEM-derived elevation for the _same ride_ routinely differ by tens of
metres, so including it would guarantee the two never match — making the fingerprint
strictly weaker than advertised while appearing more precise. Distance is rounded to
10 m for the same reason at smaller scale.

**The fingerprint is NULL unless it carries enough information to be worth a bucket:
`started_at` plus at least one positive metric (`duration_seconds` or
`distance_meters`).** Sport and a start time alone would put every sparse record into
a bucket the scorer then has to score pairwise, which is the cost the prefilter exists
to avoid — and a text extract carrying nothing but a sport and a guessed time would
share a bucket with genuinely detailed recordings. A NULL fingerprint costs only the
prefilter: such rows are still reachable through the ordinary date-windowed candidate
query and scored on their fields like anything else. (It costs nothing in safety
either way, since the fingerprint cannot reject.)

Without a start instant the remaining fields are far too weak to be worth indexing on: an athlete who runs 45
easy minutes twice in one day (doubles are ordinary in endurance training) produces
two identical field sets. Such pairs are still reachable by the ordinary date-windowed
candidate query and are scored on their fields like any other — a NULL fingerprint
loses a prefilter, not a code path.

Per AGENTS.md a GPX _recording_ always spans a positive interval, and a file that
spans none is classified as a course and never reaches `activities` at all, so files
normally do carry one.

### The evidence/state boundary, enumerated and enforced

"One immutable row per ingested input" is too loose to build against on its own,
because the same row also carries lifecycle fields that must change. Calling the whole
record immutable while retiring, reparenting, and superseding it is exactly the kind of
prose-only guarantee this design elsewhere refuses to rely on. So the boundary is
enumerated, and it is enforced in the database rather than by convention.

**Requirement.** Values received from an ingestion source are never rewritten in
place. Every field that resolves, retires, supersedes, or overrides those values lives
in the narrow mutable set below, and nowhere else.

| Class                                                               | Fields                                                                                                                                                                                                           | Rule                                                                                  |
| ------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| **Immutable evidence** — what this input said                       | `id`, `user_id`, `origin_activity_id`, `provider`, `ingest_format`, `fidelity_rank`, `external_id`, `object_key`, `content_hash`, `payload_fingerprint`, `fields`, `raw_extraction`, `recorded_at`, `created_at` | Written once at insert. **Any `UPDATE` that changes one is rejected by the database** |
| **Mutable state** — how the system currently resolves that evidence | `activity_id` (group membership), `retired_at` (lifecycle)                                                                                                                                                       | Written only by the merge/bridge/retire RPCs, under the documented lock order         |
| **Trigger-managed**                                                 | `updated_at`                                                                                                                                                                                                     | `set_updated_at`, as everywhere else in this schema                                   |

Two mutable columns is the entire surface. If a future requirement needs a third, it
gets added to this table and to the guard below in the same change, or it does not
ship.

**Enforcement.** A guard trigger, not a comment:

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

Subtracting the mutable keys rather than listing the immutable ones is deliberate: a
column added later is immutable by default, so forgetting to update the guard fails
closed. A trigger fires regardless of the caller's privileges, so the `security
definer` RPCs are bound by it too — which is the point, since they are the only
writers.

**The guard constrains what FK actions an evidence column may carry, and this is a
constraint on future schema evolution rather than a detail of this migration.** A
cascading `set null` or `on update cascade` is an `UPDATE`, so it fires this trigger
and raises `22023` — aborting the parent operation entirely. No evidence column may
therefore be given either action. `origin_activity_id` is `on delete cascade` for that
reason: it is the only action that does not attempt to rewrite the row.

**That cascade has a consequence worth stating before someone hits it.** After a
bridge, a source can have `activity_id = A` while `origin_activity_id = B`. Deleting B
would cascade the source away even though it is a live member of A, which would then
silently recompose one source short. Nothing can reach that today — there is no
`delete_activity` in the repo and this design adds none, and the only live cascade is
the whole-account delete from `athlete_profiles`, where losing both rows is correct.
But **any future individual-activity delete must be group-aware**: it has to reparent
or retire the sources it would orphan, and cannot lean on this FK to do the right
thing. Same hazard class as the `superseded_by_activity_id` self-FK discussed under
"Changes to `activities`", and it deserves the same explicit note rather than being
rediscovered in Phase 1.

This is a second trigger in a schema that has only `set_updated_at`, and that is a
real cost. It is accepted because the alternative enforcement — column-level
`revoke update (…) from service_role` — is bypassed by exactly the `security definer`
RPCs that do all the writing, and would therefore enforce nothing where it matters.

**One live override per activity is a constraint, not a convention.** The
retire-then-insert rule below is exactly the kind of two-step a writer can half-apply
— an error between the two statements, or a second writer racing the first, leaves two
live rank-0 sources. The reconciler's tie-break prefers the _earlier_ `created_at`, so
the athlete's **older** edit would win and their correction would appear to have been
ignored. `activity_sources_one_live_override_idx` above turns that into a `23505` at
the moment it happens. Note this is not a rejection key in the sense the scoping
section forbids: it constrains a row the athlete's own edit created, on a path where
the correct recovery is to retire the previous override and retry, and no ingested
workout can be lost to it.

**Athlete overrides are append-only too, and this corrects an earlier shorthand.** The
override-source section below once described clearing an RPE as "just a jsonb update".
Under this boundary it is not: an athlete edit **inserts a new `athlete_override`
source and retires the previous one in the same transaction**, so at most one override
source is ever live for an activity. Recompose is unchanged — it reads the live set —
and the edit history comes free, in the same shape as every other source. It also
removes an ordering hazard: all override sources share `fidelity_rank = 0`, and the
reconciler's tie-break prefers the _earlier_ `created_at`, so two live overrides would
resolve to the athlete's **oldest** edit rather than their newest. Retiring on insert
means that tie never arises.

**The accepted consequence.** `provider`, `ingest_format`, and `fidelity_rank` are
evidence, so improving provider inference later cannot re-label rows in flight — it
takes a backfill migration that recomputes them, reviewed as a migration. That is
slower than an in-place rewrite and is the intended trade: a label that can silently
change underneath a stored fidelity ordering is a label that can silently change which
source won a field.

### Changes to `activities`

```sql
alter table public.activities
  add column source_count integer not null default 1,
  add column field_provenance jsonb not null default '{}'::jsonb,
  add column presentation_state text not null default 'active'
    check (presentation_state in ('active','superseded')),
  add column superseded_by_activity_id uuid
    references public.activities(id) on delete set null,
  add column materialized_at timestamptz,
  add column load_rebuild_pending_from date;
```

**The self-FK must be `on delete set null`, not `restrict`.** `activities.user_id` is
`on delete cascade` from `athlete_profiles`, so deleting an account cascades into
`activities` — and `restrict` does _not_ yield to a cascading parent delete. A
`restrict` self-FK would make account deletion fail outright for any athlete who has
ever had a superseded activity. That is a live bug, not a hypothetical.

---

## How it works

### N = 1 (the common case)

1. File arrives at `process_uploaded_file_endpoint` (`api/index.py:1623`), parsed by
   `parse_gpx`/`parse_fit`/`parse_tcx`. A `ParsedCourse` still returns without
   persisting anything (unchanged AGENTS.md invariant).
2. Compute `content_hash` from the bytes already in hand and `payload_fingerprint`
   from the parsed fields.
3. Look for a grouping candidate (below). None found.
4. Insert one `activities` row **and** one `activity_sources` row in a single RPC.
   `source_count = 1`, `field_provenance` maps every field to that one source id.
5. `_finalize_persisted_activity` → `_try_match_activity_to_plan` as today.

The materialized row is byte-for-byte what it is today. Nothing about the calendar,
compliance, or the coach changes for single-source activities.

### Exact duplicate

Steps 1–2 identical, then **select-then-insert**. Only authoritative identifiers are
consulted here; `payload_fingerprint` is deliberately absent from this query, because
this is the one path that can refuse to store an athlete's workout:

1. `select activity_id from activity_sources where user_id = … and content_hash = …
and retired_at is null` — extended with a `provider`-scoped `external_id` match once
   a provider supplies one. An external id is an identity claim only _within_ the
   provider that issued it, which is exactly why it carries a scope that a byte hash
   does not.
2. If found → return **409** naming that `activity_id`.
3. Otherwise insert. The partial unique index remains the **race backstop**: on a
   concurrent double-submit the insert raises `23505`, which is caught locally
   (documented at the catch site, per AGENTS.md) and the same select is re-run to
   recover the id.

The lookup is required, not belt-and-braces: a `PostgRESTAPIError` for `23505` carries
the constraint name and a detail string, **not** the colliding row — so the central
handler alone can only produce a bare conflict, never _"you already logged this ride
on June 1."_ Two round trips is the honest cost of a useful message.

**Re-uploading after an un-merge succeeds, by design.** The `content_hash` unique
index is partial on `retired_at is null`, so a retired source neither collides nor is
found by the lookup. An athlete who un-merges and then re-uploads gets a fresh source rather
than a 409 telling them about a record they deliberately removed.

### N > 1 (merge)

A new source arrives and matches an existing activity. It is inserted with
`activity_id` pointing at that activity, then `recompose_activity` recomputes the
projection from the full live source set.

Worked example — a Garmin FIT upload of a ride already synced from Intervals.icu:

```text
activity_sources                          activities  (the materialized view)
─────────────────────────────────────     ─────────────────────────────────────
S1 intervals / intervals_api  rank 4      sport            cycling
   started_at  09:02:11                   started_at       09:02:07   ← S2 (rank 1)
   duration    3607                       duration_seconds 3612       ← S2
   distance    42180                      distance_meters  42184      ← S2
   avg_hr      —                          avg_hr_bpm       148        ← S2
   tss         71                         avg_power_watts  212        ← S2
                                          tss              <recomputed>
S2 garmin / fit               rank 1      rpe              7          ← S3
   started_at  09:02:07                   athlete_notes    "legs ok"  ← S3
   duration    3612                       source_count     3
   distance    42184                      presentation_state active
   avg_hr      148
   avg_power   212                        field_provenance {"avg_hr_bpm": "S2", …}

S3 athlete / athlete_override rank 0
   rpe 7, athlete_notes "legs ok"
```

Both device rows stay intact and immutable. The athlete sees one ride.

---

## Field-level merge precedence

Source fidelity, highest first:

```text
athlete_override (0) > fit (1) > tcx (2) > gpx (3) > intervals_api (4)
                     > text (5) > screenshot (6) > manual (7)
```

After fidelity, a total and stable tie-break: earlier `created_at`, then
lexicographically smaller `id`. The reconciler sorts the complete source set by this
order every time and never relies on call order or JSON iteration order — which is
what makes recompose a **pure function of the source set**, independent of the order
sources arrived or were retired.

| Field group                                                                                                                                                                                                             | Rule                                                                                                                             |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Device metrics — `duration_seconds`, `distance_meters`, `elevation_gain_meters`, `avg_hr_bpm`, `max_hr_bpm`, `avg_power_watts`, `normalized_power_watts`, `avg_pace_sec_per_km`, `avg_cadence_rpm`, `zone_distribution` | First non-null value in fidelity order                                                                                           |
| `started_at`, `activity_date`                                                                                                                                                                                           | Highest-fidelity source that has one — a FIT timestamp is authoritative                                                          |
| `sport`                                                                                                                                                                                                                 | Highest-fidelity source that declares a real sport. `"general"` is treated as _undeclared_, not as a conflicting value           |
| Derived — `tss`, `intensity_factor`                                                                                                                                                                                     | **Recomputed** from the merged inputs, never copied from a source                                                                |
| Athlete-authored — `rpe`, `athlete_notes`, `fatigue_notes`, `fueling_notes`                                                                                                                                             | Only ever written from the `athlete_override` source. **If no override source exists, recompose leaves these columns untouched** |
| `activity_summary` jsonb                                                                                                                                                                                                | Deep-merged lowest→highest fidelity, so the winner overwrites a conflicting leaf                                                 |
| `source`, `source_file_key`, `raw_extraction`                                                                                                                                                                           | Left as the originating source's. Full provenance lives in `activity_sources`                                                    |
| `planned_workout_id`                                                                                                                                                                                                    | Never field-merged. Transferred only by the explicit link RPC under lock                                                         |
| Everything else                                                                                                                                                                                                         | Untouched. Catch-all: any column not named above is never derived                                                                |

### Athlete edits as an override source — and why recompose can't own those columns yet

Modelling an athlete edit as a `provider='athlete'` source row makes recompose a
_total_ function over sources: one algorithm, no special-case branch. Per the
evidence/state boundary above, an edit **inserts a new override source and retires the
previous one**, rather than updating a row in place; clearing an RPE is an insert
carrying a null. That is the target shape.

But `activities.rpe` / `athlete_notes` are written directly **today** by
`repo.update_activity` and by `merge_activity_text_update`
(`backend/services/activity_text.py`). If recompose owned those columns before those
writers were converted, the first device sync after an athlete note would silently
erase the note.

So the rule above is load-bearing, not a hedge: **recompose writes an athlete-owned
column only when an `athlete_override` source is present in the version map.**

**That rule protects athlete _columns_, but not the rest of the row — which forces
the phase order.** `merge_activity_text_update` handles date corrections by calling
`repo.update_activity`, which writes the whole `Activity` model — including derived
and device fields — straight to the row, bypassing recompose entirely. If Tier-A
auto-merge shipped first, the next recompose would silently revert the athlete's date
correction.

So the writer conversion must land **before** any recompose can run. That is why it is
Phase 3 below and merging is Phase 4 — the ordering is a correctness constraint, not a
preference.

---

## Grouping predicate

The scorer lives in a new `backend/services/activity_dedup.py`, **pure and
side-effect-free**, mirroring `match_activities_to_workouts`
(`backend/services/compliance.py:85`). It takes candidate sources/activities and
returns proposed groups; it persists nothing and is testable without a database.

| Tier  | Condition                                                                                                                                                                                                                                                                                                                                                                     | Action                  |
| ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------- |
| **A** | Casefolded sport equal (or one side `general`); both `started_at` present and within **±10 min**; both `duration_seconds` present, positive, and within **±5%**; and **no metric disagreement** — for distance, either both are positive and within **±5%**, or both are zero, or at least one is null. A positive value against a zero is a mismatch and disqualifies Tier A | Auto-merge at ingestion |
| **B** | Same sport (or `general`); `activity_date` within **±1 day**; duration **or** distance within **±10%** over whichever both carry                                                                                                                                                                                                                                              | Propose to the athlete  |
| —     | anything else                                                                                                                                                                                                                                                                                                                                                                 | Not a group             |

±1 day matches `MATCH_MAX_DAY_OFFSET = 1` (`compliance.py:24`); casefolded sport
equality matches `_pair_score` (`compliance.py:53`).

**Null degradation.** A missing `started_at` or `duration_seconds` on either side
prevents Tier A and routes the pair to Tier B. In Tier B a field null on one side is
skipped rather than scored as a mismatch — but Tier B still requires at least one
actual metric agreement, since same-sport-same-day alone describes two genuinely
different workouts just as well as it describes a duplicate.

**Zero is a value, not a null, and the same three-way rule applies at both tiers.**
The comparator is `|a − b| ≤ tol × max(a, b)`, so no percentage comparison ever
divides. Every metric resolves to exactly one of three outcomes, and the earlier Tier A
phrasing — "when both distances are present and non-zero" — was wrong because it
collapsed the third into the second:

| Pair              | Outcome                  | Effect                                                                          |
| ----------------- | ------------------------ | ------------------------------------------------------------------------------- |
| both positive     | compared against `tol`   | agreement or mismatch                                                           |
| both zero         | **no comparable metric** | drops out exactly as a null does; the pair must find agreement elsewhere        |
| one zero, one not | **mismatch**             | disqualifies Tier A outright; in Tier B it counts against the pair, not skipped |

The reading being corrected mattered: "present and non-zero" made a `0`-vs-`5000 m`
pair _skip_ the distance check, so two sessions agreeing on sport, start time, and
duration would **auto-merge** despite one recording five kilometres and the other
none. That is the pairing a trainer ride and an outdoor ride of the same duration
produce, and auto-merge is the one tier with no athlete confirmation to catch it.

`duration_seconds` is held to the same rule and additionally must be positive on both
sides for Tier A: a zero-duration recording carries no evidence that it is the same
session as anything. This matters
because a trainer ride or a pool swim legitimately records `distance_meters = 0` on
both sides, and zero — unlike null — passes a naive "both rows carry it" test;
counting `(0, 0)` as agreement would let same-sport-same-day form a group.

### Hard negatives — never grouped, at any tier

- **Two sources sharing a ZIP archive's `object_key`.** Every member of one archive
  shares it (`api/index.py:1786-1789`), so this reliably means _two distinct workouts
  uploaded together_. Accepted limitation: a ZIP containing the same ride as both
  `.fit` and `.gpx` will never merge. That is the right trade — weakening it reopens
  the brick-workout false positive, which is the failure mode that actually costs the
  athlete a workout.
- **Already linked to different plan workouts.** The athlete or coach has asserted
  these are separate sessions.
- **Sport conflict between equal-fidelity sources** (next section).

### Sport disagreement

Sport is a **hard casefolded equality gate** in `_pair_score` (`compliance.py:53`), so
a wrong merged sport makes an activity silently fail to match its planned workout —
and AGENTS.md already documents this as a known failure mode.

Rule: sport comes from the highest-fidelity source that declares one. If two sources
of **equal** fidelity declare _different_ sports, that is a hard negative — do not
group them at all. Never guess.

`"general"` — the honest fallback AGENTS.md mandates when a file does not declare a
type — is treated as _undeclared_, so a `general` source may join a `cycling`
activity and two `general` sources may group. It is not a sport that can conflict.

---

## The recompose RPC

Following `20260806003910_unlink_plan_workout_from_activity_atomic.sql`:
`security definer`, `set search_path = ''` fully qualified, `p_`-prefixed args,
composite row return, documented lock order, explicit revoke/grant.

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
  (serialization_failure) **without writing**. The service re-reads, re-derives, and
  retries a bounded number of times. This closes the read/lock gap: two concurrent
  recomposes may compute from the same snapshot, but only the first commits.
- Applies `p_fields`, sets `source_count`, `materialized_at`, `field_provenance`.
- Skips athlete-owned columns unless the override source is in the map.
- An identical repeat returns current state (idempotent).

**A retry after an unseen commit resolves by re-reading, not by an idempotency key.**
If the RPC commits and the response is lost to a timeout, the client's original
`p_expected_source_versions` is now stale, so a blind resend raises `40001` and would
loop until the retry budget ran out — reporting failure for work that succeeded. The
existing rule already prevents that, but only because of a property worth stating
outright: **the service re-reads and re-derives before each retry, and recompose is a
pure function of the live source set.** So the retry recomputes byte-identical
`p_fields` from the same sources, sends the _current_ versions, and the RPC applies a
no-op and returns current state. Convergence, not luck.

This is why no idempotency key or request id is introduced. One would add a table, an
expiry policy, and a second thing to keep consistent, to reconstruct a result that
re-derivation already produces exactly. The requirement it places on the
implementation is narrow and testable: a retry must **never** resend a cached payload,
and the retry loop must re-read inside the loop rather than above it.

**Merge policy stays in Python**, in `activity_dedup.py`. The RPC owns atomic
validation and persistence, not policy — matching how every other derived artifact in
this schema is computed application-side.

Two SQLSTATE facts to design around, both verified in `_postgrest_http_status`
(`api/index.py:135-181`):

- `P0002` (the house "not found" code) maps to **503, not 404**. So "activity not
  found" must be pre-checked in Python or caught locally, not left to the handler.
- `40001` also falls to 503. That is acceptable _only because_ it is retried in Python
  and must never reach a client — if it does, that is a bug, not a degraded response.

---

## Reads: one predicate

`presentation_state = 'active'` is added to **`list_activities` and
`list_activities_between` only** (`backend/repos/supabase_repo.py:496`, `:513`).

Those two methods are the funnel for the calendar, compliance, training load,
recalibration, and the coach's `get_recent_activities` — verified by tracing every
read path — so one predicate collapses every view at once.

It **is** also applied to the new `list_dedup_candidates`: a superseded activity must
never come back as a merge candidate, or merges cycle.

Deliberately **not** applied to:

- `get_activity` — audit and un-merge must be able to fetch a superseded row by id.
- `list_synced_intervals_keys` — a superseded Intervals row must still block
  re-syncing the same `intervals:{id}`, or the next sync recreates the duplicate.

The frontend needs no schema change to keep working: `calendarActivitySchema`
(`lib/schemas.ts:170`) is a `z.looseObject`, and `components/coach-calendar.tsx` reads
only `sport`, `id`, `activity_date`, `duration_seconds`, `distance_meters`, `tss`,
`avg_hr_bpm`, `rpe`, `athlete_notes`. A "merged from N sources" affordance is a
deliberate addition (Phase 6), not a requirement for correctness.

---

## The hard part: bridging merges

Source C arrives and matches **both** existing activity A and activity B. One of them
must stop being presented — and `plan_workouts.actual_activity_id` is
`on delete set null`, so deleting the loser would **silently unlink a completed
workout from the athlete's plan**.

Three rules:

**1. Auto-bridging is forbidden.** Tier A may attach a new source to an existing
activity, but it may **never** combine two activities that each already exist. Two
activities that independently attracted sources are by construction ambiguous, and
the downside is asymmetric: a missed merge is a visible duplicate the athlete can
report, a wrong merge silently destroys a plan link. Bridging is always Tier B.

**2. Nothing is ever deleted.** The losing activity is marked
`presentation_state = 'superseded'` with `superseded_by_activity_id` set. The FK stays
valid, reads drop it via the single predicate, the row remains fetchable by
`get_activity` for audit, and the operation is reversible.

**3. Survivor selection and plan links are explicit, under lock.**

- If exactly one side owns a plan link → that side survives, so no link moves.
- If neither owns one → earlier `created_at` survives, tie-broken by
  lexicographically smaller `id` (the same total order the reconciler uses).
- If **both own different plan workouts** → reject with `22023`. The athlete must
  unlink one first. This is the honest answer: we cannot silently discard one of two
  explicit assertions that these were separate sessions.
- Any link that does move is transferred bidirectionally inside the same transaction,
  after locking the `plan_workouts` row first and verifying `actual_activity_id`
  points back. `unlink_plan_workout_from_activity` already raises `22023` when the two
  sides disagree (`20260806003910_…:51-58`), so a one-sided link must never be
  propagated.

**4. The superseded activity's sources are reparented to the survivor, in the same
transaction.** This is the rule that makes bridging actually merge anything, and
omitting it is a silent-data-loss bug rather than a gap: `recompose_activity(A)`
derives from _A's_ live source set, so if B's sources kept `activity_id = B` they
would contribute nothing to the presented row. The athlete would see B disappear from
the calendar and A keep exactly the numbers it already had — B's richer metrics
dropped on the floor, with `source_count` understating the group. Marking B superseded
without moving its sources hides a workout instead of merging it.

So the bridge transaction sets `activity_id = A` on every non-retired source of B,
then recomposes A from the combined set. Reparenting rather than traversing
`superseded_by_activity_id` at read time is deliberate: recompose is defined as a pure
function of the rows where `activity_id = <target> and retired_at is null`, and that
definition holds only if membership is a stored fact. A recompose that had to chase a
supersession chain would make its own input depend on the depth of that chain, and
every future reader of `activity_sources` would inherit the same traversal.

**This does not weaken the immutability of a source.** `activity_id` is _membership
state_, not ingested evidence — which is why the source record carries both it and an
`origin_activity_id`, set once at insert and never written again. The bytes, the
extracted `fields`, the hashes, and `raw_extraction` are untouched by a bridge. See
the evidence/state boundary section for the full split.

**Reversal restores membership from `origin_activity_id`.** Un-bridging clears B's
`presentation_state`/`superseded_by_activity_id`, returns each reparented source to
its origin activity, and recomposes both rows. Because reparenting only ever moves a
source _away_ from its origin and the origin is immutable, this is exact rather than
reconstructed — the same reasoning that makes un-merge re-derive rather than replay a
snapshot.

---

## Training load rebuild

Merging changes historical daily TSS, so `daily_load_snapshots` must be rebuilt — and
**blocker A above means it currently cannot be**.

1. **Seed at a date.** Add `get_load_snapshot_on_or_before(user_id, sport, on_date)`
   and seed `initial_ctl`/`initial_atl` from the day _before_ `since`, so a backward
   window rebuild is actually correct.
2. **Rebuild `[rebuild_from, today]`** where `rebuild_from` is the earliest
   `activity_date` on either side of the transition. A Tier-B pair can span two days,
   so using only the post-merge date would leave the other day's snapshot inflated.
3. **Outside the merge transaction, and recoverable rather than atomic.** Rebuilding
   reads every activity from `rebuild_from` forward and rewrites a window of
   snapshots — far too much work to hold under the activity row locks. So the failure
   boundary is owned explicitly:
   - The merge writes `load_rebuild_pending_from` **inside** its transaction, taking
     the **earliest** of the stored value and its own `rebuild_from` so a pending
     window is only ever widened.
   - Recompute clears it with **compare-and-swap**: `update … set
load_rebuild_pending_from = null where id = … and load_rebuild_pending_from =
<the value this run captured>`. An unconditional clear loses work — recompute
     reads a marker, rebuilds for several seconds, and a merge committing in that
     window writes an earlier marker that the clear then erases, leaving an inflated
     snapshot with nothing recorded to say so. Because the marker only ever moves
     earlier, a failed CAS means strictly more work is outstanding than this run did,
     so the correct response is to leave it pending; the next run picks it up.
   - Recompute is idempotent — it derives snapshots from the activity rows, so
     re-running converges.
   - Any later merge or recompute for that athlete starts from the **earliest**
     outstanding `load_rebuild_pending_from`, so a dropped rebuild is absorbed by the
     next one rather than needing a dedicated retry path.
4. **Bounded by a 90-day horizon** (≈ 2 × `CTL_DAYS`). A session's residual
   contribution decays as `(41/42)^N` since `ctl += (tss - ctl) / 42`
   (`backend/engine/training_load.py`): at 42 days a duplicated 100-TSS session still
   moves CTL by ≈0.9, at 90 days by ≈0.27 — below what the athlete can perceive, and
   the calendar only shows 42 days of history anyway. When the rebuild is skipped, say
   so in the merge result rather than staying silent.

This is the one place the system is eventually rather than immediately consistent, and
the inconsistency is confined to a derived, recomputable table — never to the source
rows.

---

## Migration and backfill

**Migration (additive, non-breaking).** Create `activity_sources`, add the
`activities` columns, create the RPCs. Existing readers keep working untouched because
`presentation_state` defaults to `'active'`.

**Backfill.** One `activity_sources` row per existing `activities` row:

- `origin_activity_id` = `activity_id` = the existing row's id
- `provider` / `ingest_format` derived from `source`
- `object_key` ← `source_file_key`; `external_id` ← the id inside `intervals:{id}`
- `fields` ← the row's own metric columns
- `payload_fingerprint` computed from stored fields **when `started_at` is present**
- `content_hash` **NULL** — the original bytes are in R2 but re-downloading and
  hashing every historical file is disproportionate. Consequence: a historical file
  re-uploaded after cutover is not rejected as an exact duplicate; it is stored and
  reaches the athlete through the tiered merge instead.

**The backfill cannot collide, and this is a direct consequence of the fingerprint
rule.** `payload_fingerprint` carries no unique index, so historical duplicates —
which by definition produce equal fingerprints, and which this migration deliberately
leaves in place — backfill without conflict. `content_hash` is uniformly NULL, and
NULLs are distinct in a unique index. So every historical activity backfills as an
active, unmerged row with exactly one source, and no legacy row is dropped or
namespaced to get there. Had the fingerprint been a uniqueness key, the backfill would
have had to either fail on the very duplicates that motivated this work or invent a
separate legacy namespace for them; that dilemma is designed out rather than
resolved.

Run in batches; preview and production are separate Supabase projects and must each be
linked and applied independently.

**Historic duplicates are not retro-merged by the migration.** They are surfaced by
the opt-in backlog pass (Phase 5) and confirmed by the athlete. Silently restating
someone's training history at deploy time is worse than leaving it and asking.

Per AGENTS.md, `docs/supabase-migration-history.md` is updated **in the same change** —
both the "Canonical migration sequence" bullet and a section with
`**File:** / **Change:** / **Why (issue #N):** / **Security note:** / **All environments:**`.

---

## Phases

Each phase is independently shippable and ends in something verifiable.

**Phase 0 — unblock.** Fix the three verified bugs: `get_load_snapshot_on_or_before` +
seed-at-date in `recompute_load_endpoint`; `_activity_source_for_filename` fallback.
Also replace the load rebuild's display-oriented `list_activities(..., limit=500)`
candidate query with an unbounded keyset-paginated one (ported item 3 below).
_Verifiable:_ a backward window rebuild produces correct CTL; a `.fit` file with no
suffix saves instead of 503-ing; a rebuild window holding more than 500 activities
still includes the oldest of them in the rebuilt snapshots.

**Phase 1 — schema.** Migration + backfill + `Activity` model fields + repo methods.
No behaviour change. _Verifiable:_ `bun run db:reset` replays clean; **every
`activities` row has exactly one non-retired source whose `fields` round-trip to the
values the row already holds** — that invariant, not "existing tests still pass", is
what catches a bad backfill mapping.

**Phase 2 — write path.** Every ingestion path writes `activity_sources` alongside
`activities`. Hashing and fingerprinting wired in. Select-then-insert exact-duplicate
409 on authoritative identifiers only. Still no merging. _Verifiable:_ re-uploading a
file returns 409 naming the existing activity; re-uploading a ZIP rejects every member;
two distinct ZIP members both save; **the same ride uploaded as both `.fit` and `.gpx`
stores two sources rather than rejecting the second**; and **two distinct sessions that
collide on `payload_fingerprint` are both stored** — the fingerprint never rejects.

**Phase 3 — athlete overrides (must precede any recompose).** Convert
`repo.update_activity`, `merge_activity_text_update`, **and
`build_activity_from_text`** to write an `athlete_override` source and route through
recompose. The third is easy to miss and is not optional: it populates `rpe`,
`athlete_notes`, and `fueling_notes` directly on a `text_extract` activity today
(`backend/services/activity_text.py:667-669`), so an athlete who describes a session
in chat gets athlete-authored values on a source whose `ingest_format` is `text`, not
`athlete_override`. Left unconverted, the merge rule below — athlete columns come only
from an override source — would read those values as belonging to no override and
leave them untouched on first recompose, then drop them the moment a real override
source appeared and recompose began owning the columns. _Verifiable:_ an athlete note
and a corrected date both survive a subsequent recompose, **including a note that
originated from a chat text extract rather than an explicit edit**. See the writer-conversion
note above — shipping merge before this silently reverts date corrections.

**Phase 4 — materialization.** `activity_dedup.py` scorer + reconciler,
`recompose_activity` RPC, Tier-A auto-merge wired into `_finalize_persisted_activity`
**before** `_try_match_activity_to_plan` (so a duplicate never steals the planned
workout from the surviving activity — this ordering is load-bearing), and the
`presentation_state` read predicate. _Verifiable:_ a FIT upload of an already-synced
Intervals ride yields one calendar entry with the FIT's richer numbers; compliance
stops reporting the phantom unplanned session.

**Phase 5 — Tier B + bridging.** The server-side consent protocol in full (ported
item 1: proposal record, server-issued normalized `expected_phrase`,
orchestration-injected message id, group-scoped consumption), coach tools
(`find_duplicate_activities`, `merge_activities`, `unmerge_activity`) in
`lib/agent/tools.ts` routed via `postEngine` in `lib/agent/coach-tools.ts`, system
prompt guidance, bridging rules. _Verifiable:_ the coach can surface the backlog and
merge only after explicit athlete confirmation; a both-sides-linked bridge is refused.

**Phase 6 — UI.** "Merged from N sources" affordance in
`components/coach-calendar.tsx`. Must survive the narrow-viewport dot mode
(`coach-calendar.module.css`, `.chipLabel { display: none }`) and the
`MAX_CHIPS_PER_DAY = 3` overflow.

---

## Hard parts, bottlenecks, and assumptions

**Assumptions being made explicitly:**

1. **Provider is mostly `unknown` at first, and nothing destructive depends on it.**
   `activities` has no provider column today; external identity is smuggled into
   `source_file_key` as `intervals:{id}`. Uploads can sometimes infer a provider from
   file internals (FIT `manufacturer`) but will often be `unknown`. This is safe
   because the only key that can reject is `content_hash`, and identical bytes are
   identical regardless of which provider label they landed under. The fingerprint,
   which is the key a wrong provider label would most plausibly distort, cannot reject
   anything. Populating a real provider later only _narrows_ what counts as an exact
   duplicate, so it is a strict improvement rather than a breaking change.
2. **The Garmin sidecar writes nothing today** (local files only, issue #388). When
   server upload lands, the Garmin activity id becomes a real `external_id` and
   becomes the strongest dedup key available. This design should not be finalized
   without checking it composes with #388.
3. **`started_at` is trustworthy across providers.** Tier A's ±10 min window assumes
   providers agree on the start instant to within minutes. Timezone or clock-skew bugs
   would make Tier A either over- or under-merge.
4. **Sport `"general"` is common enough to matter.** Treating it as _undeclared_
   rather than as a value is a real semantic choice; if most uploads land there, most
   pairs become groupable on weaker evidence.

**Hard parts, in rough order of risk:**

1. **Grouping, not dedup, is the fuzzy part.** Exact dedup is a unique index and is
   essentially free. Deciding that two _different_ records describe one workout is
   heuristic, and every tolerance in the Tier A/B table is a guess until it meets real
   athlete data. Budget for tuning.
2. **Bridging merges destroy plan links if done carelessly.** Addressed above by
   forbidding auto-bridging and refusing the both-linked case — but this is the single
   most dangerous operation in the design.
3. **The load rebuild is the only eventually-consistent seam**, and it depends on a
   bug fix (Phase 0) that is invisible until you specifically test a backward window.
4. **The coach gets exactly one tool call per turn**
   (`lib/agent/coach-tools.ts:382`, `isEnabled: !runContext.context.toolCalled`). A
   dedup conversation that needs _find → present → merge_ therefore spans multiple
   turns by construction. The Tier-B flow must be designed around that, not fight it.
5. **Backend/frontend activity shapes can drift silently.** `calendarActivitySchema`
   is a `z.looseObject` and the backend dumps the full Pydantic `Activity`, so new
   fields cross the wire unvalidated. Adding `source_count` etc. will "just work",
   which is exactly the hazard.
6. **Recompose cost grows with source count.** It re-derives from the full source set
   every time. Fine at N ≤ 5; if a provider ever fans out many sources per activity
   this needs a bound.
7. **`content_hash` is unbackfillable in practice**, so the first months after cutover
   have weaker exact-dedup on historical files than on new ones. Accepted rather than
   worked around: the alternative — letting `payload_fingerprint` reject in its place —
   trades a recoverable duplicate for unrecoverable data loss, which is the one trade
   this design refuses.

---

## Open before sign-off

Two ownership boundaries in this design are currently enforced by a table in this
document and by the Python reconciler, and nothing else. Both must be exhaustively
enumerated — and their enforcement point named — before implementation starts. A
boundary that exists only in prose is the failure mode this design criticises in the
in-place alternative, and relocating it is not the same as removing it.

**1. Projection ownership.** `activities` is described as a derived projection, but the
merge-precedence table carves out athlete-owned columns (untouched with no override
source), `source` / `source_file_key` / `raw_extraction` ("left as the originating
source's"), `planned_workout_id` (never merged), and a catch-all "everything else —
untouched". So it is a _partially_ derived projection. Required: the exhaustive,
enumerated list of which `activities` columns `recompose_activity` writes, and where
that list is enforced beyond convention. If the honest answer is "the Python
reconciler and nothing else", say so — the advantage over an in-place survivor is then
real but narrower than this document's framing implies, and Phase 3's ordering
constraint is doing more of the work than the architecture is.

**2. Source ownership.** Settled above in "The evidence/state boundary", enumerated and
enforced by the guard trigger. Listed here so the two boundaries are reviewed together
and neither is signed off in isolation.

**3. The non-destructive fingerprint requirement stands unless disproved.**
`payload_fingerprint` remains candidate-generation evidence, with no unique index,
unless someone can demonstrate collision-safe uniqueness on real athlete data —
meaning a normalization under which two genuinely distinct sessions provably cannot
produce equal fingerprints. Absent that proof, exact identifiers prevent duplicate
ingestion and heuristic similarity does not, because heuristic similarity behind a
constraint is data loss rather than a missed match.

---

## Ported from the in-place design (PR #450)

The alternative design on `design-doc-dedupe` reached these conclusions independently
of its storage model, and they hold here unchanged. They are adopted rather than
re-derived, and each is a gap in this document as it stands.

1. **Tier-B consent is a server invariant, not a prompt promise.** A short-lived
   proposal record scoped to `user_id`, holding the sorted member ids, expected member
   versions, a server-issued confirmation code, the derived `expected_phrase` stored
   already normalized, `expires_at`, and `consumed_at`. The merge RPC requires a later
   `chat_messages` row for the same user with `role = 'user'` whose normalized text
   equals `expected_phrase`; the message id is injected by trusted orchestration and is
   absent from the model-visible tool schema. A model-supplied boolean, copied
   confirmation text, or prompt instruction is never evidence of consent. Phase 5 of
   this document currently covers all of that in one clause, which is a security gap
   rather than a level-of-detail gap.
2. **Consent is group-scoped and outlives the first pairwise operation.** An
   athlete confirmed _a group_, once; a multi-step sequence must not re-ask, and must
   consume the proposal exactly once, on the final step, in the same transaction.
3. **The load rebuild must not use the display query.** Confirmed against source and
   promoted to **verified blocker C** above; Phase 0 fixes it alongside the
   seed-at-date lookup.
4. **`list_dedup_candidates` is unbounded, keyset-paginated, and self-excluding in
   SQL** — not a `limit`-capped display query with the new row filtered out in Python.
   A duplicate lying beyond an arbitrary cap is exactly the one a backlog pass exists
   to find.
5. **Merge history survives an un-merge.** Retiring a source removes it from the live
   set the reconciler reads, which this design gets nearly free — but the record of the
   merge having happened must be retained append-only, not dropped. It is the evidence
   needed when an athlete asks why their numbers moved twice, or when a bad Tier-A
   rule has to be reconstructed after the fact.

---

## Files this design will touch

| Area      | Files                                                                                                                                                                                                                                                                                                              |
| --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Migration | `supabase/migrations/<ts>_activity_sources.sql`, `supabase/migrations/<ts>_recompose_activity_rpc.sql`, `docs/supabase-migration-history.md`                                                                                                                                                                       |
| Model     | `backend/models/training.py` (`Activity` gains the new columns; new `ActivitySource`)                                                                                                                                                                                                                              |
| Repo      | `backend/repos/supabase_repo.py` — `presentation_state` predicate on `list_activities`/`list_activities_between`; new `create_activity_with_source`, `list_activity_sources`, `list_dedup_candidates`, `recompose_activity`, `get_load_snapshot_on_or_before`                                                      |
| Services  | `backend/services/activity_dedup.py` (new — pure scorer + reconciler); `backend/services/activity_text.py` (Phase 3)                                                                                                                                                                                               |
| API       | `api/index.py` — `_finalize_persisted_activity` (:2448), `_persist_extracted_activity` (:2430), `_build_uploaded_activity_or_course` (:1551), `_zip_activity_entry` (:1779), `intervals_sync` (:557), `recompute_load_endpoint` (:1439), `_activity_source_for_filename` (:1513), new find/merge/unmerge endpoints |
| Agent     | `lib/agent/tools.ts`, `lib/agent/coach-tools.ts`, `lib/agent/system-prompt.ts`                                                                                                                                                                                                                                     |
| Frontend  | `components/coach-calendar.tsx`, `lib/schemas.ts` (Phase 6)                                                                                                                                                                                                                                                        |

**Reuse rather than rebuild:** the pure-scorer shape of
`match_activities_to_workouts` (`backend/services/compliance.py:85`); the
`unlink_plan_workout_from_activity` RPC template
(`20260806003910_…`); the conditional-unique idiom from
`20260716000000_intervals_sync_idempotency.sql`; `build_activity_summary_from_fields`
(`supabase_repo.py:255`) for summary rebuilds; `recompute_load_series`
(`backend/engine/training_load.py`).

---

## Verification

**Per phase, run `bun run check` and `uv run pytest`.** Full gate before handoff:
`bun run lint`, `bun run typecheck`, `uv run ruff check .`, `uv run ty check`,
`uv run vulture`, `bun run ast-grep:check`. Pre-push runs all of these plus Playwright
— do **not** skip UI tests for Phase 6.

**Database.** `bun run db:reset` to replay migrations locally, then
`bun run test:db` for the `@pytest.mark.db` guard-clause tests (excluded from the
default run). Before remote apply: `supabase migration list --linked` and
`supabase db push --linked --dry-run`, against preview and production separately.

**New tests:**

| File                                                                                    | Covers                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| --------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/python/test_activity_dedup.py` (new)                                             | Pure scorer: Tier A/B boundaries, null degradation, the `(0,0)` = _no comparable metric_ and `(0,n)` = mismatch rules, all three hard negatives, sport-conflict rejection, `"general"` treated as undeclared. Reconciler: fidelity ordering, deterministic tie-break, athlete columns untouched with no override source, `tss` recomputed not copied, order-independence (same result whatever order sources arrived)                                                                                                   |
| `tests/python/test_supabase_db.py` (evidence guard)                                     | The immutability trigger: updating `activity_id` or `retired_at` succeeds; updating `fields`, `content_hash`, `provider`, `fidelity_rank`, or `raw_extraction` raises `22023` — including from a `security definer` RPC, which is the caller the guard exists to bind. An athlete edit inserts a new `athlete_override` source and retires the prior one, leaving exactly one live override                                                                                                                             |
| `tests/python/test_supabase_repo.py`                                                    | Extend `FakeSupabaseClient`/`FakeTableQuery` for `activity_sources` and add an `rpc()` handler for `recompose_activity` (an unknown RPC raises `AssertionError`). Assert `client.calls` exactly. Verify `response.data` is handled as a **dict** for the composite-row return. Confirm the `presentation_state` predicate applies to the two list methods and **not** to `get_activity` / `list_synced_intervals_keys`                                                                                                  |
| `tests/python/test_supabase_db.py`                                                      | RPC invariants against a real DB: cross-user rejection, the `40001` version-mismatch path, lock ordering, idempotent retry, both-sides-plan-linked bridge refused with `22023`, that the `content_hash` partial unique index fires on re-insert but not for distinct ZIP members, and that a bridge **reparents the superseded activity's sources to the survivor** — asserting the survivor's post-bridge `source_count` and that a field only B carried reaches A, which is the assertion a stranded-source bug fails |
| `tests/python/test_api.py`                                                              | Exact duplicate returns 409 naming the existing activity; **a `payload_fingerprint` collision between two genuinely distinct sessions stores both and returns no 409** — the non-destructive-fingerprint requirement, asserted where it can actually be violated; Tier-A detection runs **before** `_try_match_activity_to_plan`                                                                                                                                                                                        |
| `tests/python/test_calendar_api.py`, `test_compliance_api.py`, `test_intervals_sync.py` | Superseded rows drop out of the calendar and out of unplanned sessions; Intervals dedup behaviour is unchanged and a superseded Intervals row still blocks re-sync                                                                                                                                                                                                                                                                                                                                                      |
| `tests/python/test_engine.py`                                                           | Seed-at-date rebuild correctness; rebuild starts at the earliest affected date; the 90-day horizon; a dropped rebuild leaves `load_rebuild_pending_from` and is absorbed by the next one                                                                                                                                                                                                                                                                                                                                |
| `tests/web/agent-tools.test.ts`                                                         | The three new tool schemas (note `lib/agent/tools.ts:10-13`: nested object fields must be `.nullable()`, not `.optional()`)                                                                                                                                                                                                                                                                                                                                                                                             |
| `tests/ui/calendar.spec.ts`                                                             | The merged-sources affordance, including narrow-viewport dot mode                                                                                                                                                                                                                                                                                                                                                                                                                                                       |

**End-to-end manual check** (`bun run dev:local`): upload a FIT file → confirm one
calendar entry; upload the identical file again → confirm 409 naming the first
activity; sync the same ride from Intervals.icu → confirm still **one** calendar entry
carrying the FIT's richer numbers and `source_count = 2`; add an RPE, re-sync, confirm
the RPE survived; ask the coach for a compliance summary and confirm no phantom
unplanned session.
