# Historical training-load rebuilds

`POST /api/engine/recompute-load` rebuilds derived CTL/ATL/TSB snapshots for the authenticated
athlete. This is the manual rebuild foundation for #447; automatic ingestion and deduplication
triggers remain separate work.

```json
{ "since": "2026-07-01", "sport": "cycling" }
```

Omit `sport` (or use `null`) for the aggregate series. Omit `since` to rebuild today. Explicit
historical requests are not subject to the proposed 90-day automatic-merge horizon. Future start
dates return 422 before database access.

## Read and write contract

1. Read the latest snapshot on or before the day preceding `since`, scoped to the same athlete
   and sport series. Never seed from today's snapshot.
2. If the seed is older than that day, replay the entire gap starting the day after the seed,
   including both activity load and rest-day decay. With no seed, start from zero at `since`;
   request the beginning of the athlete's history for a full-history rebuild.
3. Read compact `id,activity_date,tss` inputs through the captured current date. The dedicated
   repository reader uses ascending immutable UUID keys and accumulates daily totals. It does
   not reuse the newest-first display query or its 500-row cap. Pages request 500 rows, but only
   an empty page ends the scan, so lower PostgREST row limits are safe. Null TSS contributes zero;
   this operation does not derive missing TSS from sensor data.
4. Finish and validate every input page before computing and upserting the snapshots. A failed
   page cannot cause a partially read history to overwrite existing snapshots. Transport failures
   return 503; PostgREST errors use the shared SQLSTATE error contract. Invalid database inputs
   fail closed.
5. Upsert the computed window in one database request. Migration
   `20261011183239_load_snapshot_null_identity.sql` ensures that aggregate `sport = NULL` keys
   conflict just like concrete sport keys. Repeated requests replace the same logical rows.

The response retains `snapshots_written`, `rebuild_start`, `seed_date`, `seed_ctl`, `seed_atl`,
`latest_ctl`, `latest_atl`, and `latest_tsb`. A null `seed_date` explicitly signals the zero-seed
fallback. Existing snapshots round values to one decimal place; reseeding from a rounded snapshot
can differ by that rounding from a continuous full-precision computation.

## Concurrency and remaining work

Pagination is not a transaction spanning all reads. Concurrent insertions, deletions, or TSS/date
changes can invalidate a scanned window. Automatic writers must coordinate and retry invalidated
windows using the pending-work design in `ACTIVITY_MATERIALIZATION_DESIGN.md`. This endpoint
does not add that queue, merge/unmerge hooks, or the automatic horizon policy. It also does not
exclude duplicate activities that have not yet been materialized under that design.

All input pages are read before the single snapshot upsert. A timeout during input collection
leaves snapshots unchanged. Large rebuilds remain synchronous and subject to the deployment's
request budget; durable scheduling is separate work.
