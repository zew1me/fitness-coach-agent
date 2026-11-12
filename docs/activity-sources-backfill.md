# Activity-source backfill operator runbook

Issue #533, Phase 1 / PR B of [the materialization design](ACTIVITY_MATERIALIZATION_DESIGN.md).
This tool captures historical inputs; it does **not** merge activities, recompute load, download
files, change presentation, or perform the gated PR C contract step.

## Current boundary

- PR A (#534) installed the shadow writer and temporary legacy-insert capture trigger.
- Migration `20261104061335_activity_sources_backfill.sql` defines the progress singleton and
  preflight/batch/verify RPCs. Its exact applied SQL was restored to `main` in `998e52e`; do not
  modify that historical migration to change an already-deployed function.
- This CLI invokes those RPCs. SQL, not local files, owns the checkpoint. Each Supabase project
  has independent progress. Migration installation is **not** evidence that the backfill ran.
- PR C remains gated on separately recorded completion and absence of legacy-trigger captures
  in **both** environments. The trigger, direct-insert privileges, and progress table remain intact.

## Installation and target selection

```bash
uv sync --package fitness-coach-activity-sources-backfill
uv run --package fitness-coach-activity-sources-backfill activity-sources-backfill --help
```

Use an operator service-role credential, never an athlete JWT or a browser key. Load
`BACKFILL_SERVICE_ROLE_KEY` from your private environment/secret manager; do not put its value on
the command line, in a ticket, or in a transcript. The tool does not load `.env.local`, infer a
linked project, or accept a credential flag.

Set `BACKFILL_SUPABASE_URL` to the **reviewed** Supabase origin, or supply `--url`. Every command
requires `--confirm-target` matching its exact host (including port when present):

```bash
# Replace the placeholder after reviewing the environment; no key is shown here.
export BACKFILL_SUPABASE_URL=https://REVIEWED_PROJECT_REF.supabase.co
uv run --package fitness-coach-activity-sources-backfill activity-sources-backfill \
  --confirm-target REVIEWED_PROJECT_REF.supabase.co preflight
```

HTTPS is required except for loopback local testing. Redirects and ambient HTTP proxies are
not followed, so the service-role credential is sent only to the explicitly selected origin.
Preview and production must be reviewed and operated separately. Before any remote migration,
follow [the Supabase brief](cli-tools/supabase.md), inspect migration history and a dry run, and
obtain approval for that environment. This CLI never applies migrations.

**All three commands write state.** `preflight` records `preflight_at`; `verify` can rewind the
cursor and change completion markers. Neither is a read-only/dry-run command. Target confirmation
acknowledges the selected database; it does not replace the human rollout approval.

## Preflight, run, verify

`preflight` checks all legacy source mappings and Intervals identities. Unknown sources (including
`file_upload`), malformed `intervals:{id}` keys, or duplicate parsed identities per user abort.
Investigate the data privately; do not relabel unknown values or delete rows just to pass.

```bash
uv run --package fitness-coach-activity-sources-backfill activity-sources-backfill \
  --confirm-target REVIEWED_PROJECT_REF.supabase.co run --batch-size 500 --max-batches 10000

uv run --package fitness-coach-activity-sources-backfill activity-sources-backfill \
  --confirm-target REVIEWED_PROJECT_REF.supabase.co verify
```

`run` always repeats preflight before starting a batch, even on a resumed invocation. It:

1. Locks and captures activity rows in `(created_at, id)` order in bounded transactions.
2. Lets SQL commit each batch's sources and checkpoint together; deterministic source IDs and
   conflict comparisons prevent duplicate capture. A conflicting existing source stops the run
   rather than overwriting immutable evidence.
3. Calls global verification after the cursor drains, requiring two clean verification passes.
4. Resumes batching if verification finds a historical hole and rewinds the checkpoint. Missing
   sources also count as live-source errors; they are repaired by capture, not source deletion.
5. Stops on conflicting evidence, RPC failure, or the batch budget. It never silently clears a
   checkpoint, changes privileges, or removes the legacy trigger.

The batch RPC compares an existing deterministic source against the locked activity. If a legacy
projection edit made that comparison disagree, it fails closed. Do not rewrite captured fields
or reset progress to bypass this guard. Verification round-trips captured fields rather than
requiring mutable metrics to equal today's projection; later projection drift belongs to Phase 3.

Output is JSONL with aggregate preflight counts, batch counts, and verification results. Row
payloads, raw RPC error bodies, cursor IDs, and credentials are not printed. Treat even aggregate
operator logs as private. Exit codes: **0** successful command/completed run; **1** RPC failure,
incomplete verification, conflict, or batch-budget exhaustion; **2** invalid configuration/options.
A standalone first clean `verify` can return 1 because completion needs a second clean pass.

## Interruption and recovery

Ctrl-C, process termination, or a request timeout does not invalidate the database checkpoint.
An interrupted transaction rolls back both source inserts and the cursor. A response lost after
commit leaves the advanced cursor in the database. **Rerun the same command**; the tool does not
blindly retry an ambiguous RPC within the current process. `--timeout` (global option, default
60 seconds) controls each RPC; reduce `--batch-size` if transactions exceed it. Source inserts
and checkpointing remain atomic regardless of the client timeout.

`--max-batches` bounds work per invocation; continuously arriving/backdated rows cannot make one
invocation run forever. Hitting the budget exits 1 with progress preserved. Resume after review.

## Rollout record / contract gate

Record each environment's actual results in [migration history](supabase-migration-history.md):

| Evidence                                            | Required before PR C                                                                                                                 |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Correct linked project / applied migration versions | Explicitly checked per environment                                                                                                   |
| Backfill checkpoint                                 | `completed_at` is non-null                                                                                                           |
| Global verification                                 | Zero holes, zero live-source errors, zero capture errors; two clean passes                                                           |
| Legacy-insert telemetry                             | No `activity_sources: legacy activities insert captured by bootstrap trigger` captures over a reviewed deployment observation window |
| Writer deployment                                   | Every deployed ingestion writer uses the shadow RPC                                                                                  |
| Direct-insert test fixtures                         | Converted to the RPC before revocation                                                                                               |

Do not substitute “migration applied” or “deploy requested” for these checks. PR C must revoke
application direct INSERT first, then drop the trigger and progress table, in a separately
reviewed migration. Keep #533 open until its independently gated phases and rollout are complete.

## Local tests

Use a disposable local Supabase stack, not a hosted project. The direct-Postgres test helper
refuses non-loopback targets; `LOCAL_SUPABASE_DB_URL` can select a separate local test port.
The normal `bun run db:reset` **destroys local data**: check which local stack is selected first.
Export local-only `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` for the live API tests.

```bash
uv run pytest tools/activity-sources-backfill/tests/
bun run db:reset
bun run test:db
uv run pytest
uv run ruff check .
uv run ty check
uv run vulture
bun run check
```

Workspace registration puts CLI tests and type/lint checks under the existing root CI and hooks.
The dedicated backfill suite exercises PostgreSQL transactions, grants, and row-lock races; mocks
alone cannot establish the backfill invariants.
