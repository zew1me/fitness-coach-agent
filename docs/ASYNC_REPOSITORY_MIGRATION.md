# Async repository migration

## Goal and boundary

FastAPI handlers must await network I/O all the way through the service and repository.
An `async def` wrapper around synchronous `.execute()` still blocks the event loop.
Keep JWT/PKCE checks, payload construction, parsing, and other pure helpers synchronous.
For SDKs without async I/O, explicit thread offloading remains appropriate; do not replace
an existing safe offload merely for naming consistency.

## Completed OAuth slice (#476)

- #478 introduced `AsyncOAuthRepository`, pure helpers in `oauth_repo_base.py`, awaited
  PostgREST calls, async gateway backoff, and opt-in live repository coverage.
- `AuthService` now defaults to that implementation. `oauth_repo.OAuthRepository` and
  the package-level export refer to the same async class; the old synchronous adapter
  has been retired, rather than left available for accidental reuse.
- Consent, reauthorization, code exchange, refresh, revocation, browser-token issuance,
  and bearer grant validation await persistence. `require_user_context` is an async
  FastAPI dependency, including on non-OAuth routes.
- Browser-session verification awaits `httpx.AsyncClient.get` and closes its per-request
  client. JWT/cookie/PKCE helpers remain synchronous.
- The browser-token thread bridge is gone. Successful issuance still renews the browser
  cookie; failed issuance does not. The OAuth repository's PostgREST pool is closed by
  the application lifespan, including exceptional shutdown; unconfigured cleanup is safe.
- Gateway retry still covers the **complete read-then-write operation**, once, only for
  502/503/504. An ambiguous committed INSERT/UPDATE is observed by the next read.
  SQLSTATE `23505` is not retried. This does not fix concurrent first-insert races or
  change code-consumption / refresh-rotation atomicity.

Tests: `test_async_oauth_repo.py`, `test_async_oauth_repo_db.py`,
`test_async_oauth_flow.py`, `test_auth_flow.py`, and OAuth cases in `test_api.py`.
The route integration suite uses the real service and repository with an async fake
PostgREST boundary. Event-based tests check that pending writes and retry sleeps allow
unrelated requests to proceed. Existing HTTP/error contracts remain unchanged, including
bearer validation's existing invalid-token response handling.

## Next PR-sized slices

| Order | Complete slice                                                 | Production boundary and callers                                                                                                                                                                                                                                                                                                                                                                                                                        | Validation                                                                                                                                                                                                                                  |
| ----- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1     | Intervals connection persistence                               | `backend/repos/intervals_repo.py`: connection read, atomic replacement RPC, revoke. Make `IntervalsConnectionRepository` and the persistence-crossing methods in `backend/services/intervals.py` async (`get_status`, `resolve_auth`, `disconnect`, and replacement during code exchange). Await the status/disconnect routes and sync callers in `api/index.py`. Keep state signing and authorization URL construction synchronous. Add pool cleanup. | `test_intervals_repo.py`, `test_intervals_oauth.py`, `test_intervals_sync.py`, Intervals API cases; retain development bypass and encrypted-token behavior, and prove callback/RPC and pending connection reads yield.                      |
| 2     | Chat persistence and lease lifecycle                           | `backend/repos/supabase_repo.py`: thread/message/model-state and acquire/renew/release/status RPCs. Callers in `backend/services/chat.py` already await the repository, but its `.execute()` calls are synchronous. Introduce a typed async client boundary and migrate this entire domain, including schema-cache retries.                                                                                                                            | `test_supabase_repo.py`, `test_supabase_db.py`, chat/model-state API cases; preserve lease ownership, idempotency, version conflicts, and full-history behavior. Do not change compaction semantics.                                        |
| 3     | Athlete context, thresholds, recovery, goals, and availability | Migrate the corresponding `SupabaseRepository` queries and callers in `api/index.py` / `backend/services/goal_service.py`. Group reads and writes by a complete domain if the change is too large.                                                                                                                                                                                                                                                     | Profile, threshold, recovery, goal, and schedule repository/API tests; assert real async `.execute()` boundaries rather than just async service mocks.                                                                                      |
| 4     | Activities, training load, plans, workouts, and compliance     | Migrate the remaining `SupabaseRepository` I/O together with ingest/sync/planning consumers. Keep existing persistence semantics, pagination, atomic RPCs, and historical preservation unchanged.                                                                                                                                                                                                                                                      | Activity/load/plan/compliance repository and API tests, opt-in DB RPC tests; read `ACTIVITY_MATERIALIZATION_DESIGN.md` before touching activity writes. Coordinate with the separate #447 load-rebuild stack to avoid overwriting its work. |

For the shared `SupabaseRepository`, use a domain-scoped async client alongside the legacy
client during intermediate slices, or extract a domain adapter if needed. Do not globally
swap the client while unmigrated methods still expect synchronous responses. Remove the
legacy client only after the final domain has cut over. Each independently functional
slice should branch from current `main` and open its own PR against `main`; sharing a
file or async theme is not a dependency. Keep an adapter's client cutover, affected callers,
and async fakes together when they must land atomically. Use `gh stack add` / `gh stack submit`
only when a slice genuinely requires another unmerged PR, or combine the changes into one
PR when they cannot function independently. After a squash merge, start from current `main`
instead of replaying the merged branch's old commits.

## Not async-migration targets

- `backend/services/r2.py` already offloads boto3 operations and response-body reads
  through `run_in_threadpool`; boto3 itself remains synchronous.
- Screenshot vision uses `AsyncOpenAI`, and activity-text extraction uses async HTTP and
  async retries. Their CPU parsing is a separate performance concern, not repository I/O.
- One-shot scripts and local synchronous CLI workflows need not become async for consistency.

## Gate for every slice

1. Inspect the actual client and `.execute()` boundary, not only method declarations.
2. Update async protocols/fakes and all callers in the same PR. Add an integration test
   through the production service/repository and a deterministic pending-I/O concurrency test.
3. Preserve failure mapping, retry budgets, and ambiguous-write behavior; do not introduce
   new concurrent writes or `gather` as part of a mechanical I/O migration.
4. Own and test connection cleanup; never log tokens or service-role credentials.
5. Run repository quality gates and hooks, including UI tests for authentication or
   API contracts used by the UI. Run live DB tests only against explicitly isolated local
   or development infrastructure, never implicitly against hosted credentials.
