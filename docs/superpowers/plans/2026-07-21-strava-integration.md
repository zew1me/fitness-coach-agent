# Strava Integration Implementation Plan

> **Historical planning record:** This document retains the original
> implementation proposal and its task-level intent. The dated
> delivery-status block is the source of truth for what shipped; unchecked
> historical tasks are not rollout instructions for the delivered release.

## Delivery Status (2026-07-22)

**Shipped:** one authorized athlete can connect, view status, disconnect, and
run manual bounded summary syncs from `/profile`. The release includes
rotating-token OAuth, encrypted token persistence, idempotent provider-key
upserts, rate-limit handling, remote revocation, and deletion of the connection
and imported Strava activities on disconnect.

**Deferred / out of scope:** raw FIT imports; webhooks and all webhook-based
rollback or deauthorization handling; scheduled polling; retention jobs;
AI/agent processing; refresh-lease infrastructure (the shipped token rotation
uses its compare-and-swap contract instead); and cross-provider
fuzzy/destructive Intervals.icu deduplication. Re-sync deduplicates only by
Strava's stable provider key, so connecting both providers can show duplicate
real-world activities.

**Data semantics:** Strava summaries do not contain the athlete-specific
threshold context required for TSS, intensity factor, or zones, so those fields
remain unset.

**Goal:** Add a per-athlete Strava connection to `/profile` that follows the existing Intervals.icu interaction model—connect, status, disconnect, and **Sync now**—while implementing Strava-specific OAuth token rotation, activity pagination, rate-limit handling, webhook lifecycle, deletion, provenance, and AI-processing controls.

**Architecture:** Add a provider-specific Strava service and repository alongside the Intervals.icu implementation, but do not copy its bearer-token lifecycle directly. Store encrypted access and refresh tokens, serialize refreshes with a database lease, import only relevant summary fields into canonical activities, update existing imports idempotently, enqueue webhook events for prompt processing, and make disconnect/deauthorization revoke access and purge affected data. Enable import and AI processing independently only when athlete consent cover them.

**Initial scope:** Activity summaries only and raw FIT files. Do not fetch activity streams, detailed activity maps, GPS coordinates, segments, routes, photos, or kudos. Do not request write scopes. Manual sync pulls a bounded recent window and webhooks maintain create/update/delete state after connection.

**Tech stack:** FastAPI, Python/httpx/Pydantic, Supabase/Postgres RPCs and RLS, Next.js/React/TypeScript, Zod, Vitest, pytest, Playwright, Vercel Functions/Cron.

**Primary references:**

- GitHub issue [#340](https://github.com/zew1me/fitness-coach-agent/issues/340)
- [Strava authentication](https://developers.strava.com/docs/authentication/)
- [Strava API reference](https://developers.strava.com/docs/reference/)
- [Strava webhooks](https://developers.strava.com/docs/webhooks/)
- [Strava rate limits](https://developers.strava.com/docs/rate-limits/)
- [Strava brand guidelines](https://developers.strava.com/guidelines/)

---

## Success Criteria

- The authenticated Coach Arden user can connect the one authorized Strava athlete from `/profile` using Strava OAuth.
- Access and refresh tokens are encrypted at rest, never returned to the browser, and refreshed safely before expiry.
- `/profile` shows connected athlete, granted scopes, last successful sync, errors, **Sync now**, and disconnect controls.
- Manual sync imports a bounded activity-summary window, paginates, updates changed activities, and remains idempotent under retries and overlapping requests.
- Imported activities preserve Strava provenance.
- Rate-limit usage is observed and 429 responses are bounded and actionable.
- Webhooks handle activity create/update/delete and athlete deauthorization without exceeding Strava's response-time requirement.
- Disconnect immediately blocks further reads, remotely revokes the Strava grant, and completes the deletion behavior required by the approved authorization.
- The privacy policy and connection disclosure describe collection, subprocessors, retention, withdrawal, export, and deletion.
- Migrations, lint, type checks, unit/integration tests, Playwright, and a focused security/code review pass before rollout.

## Explicit Non-Goals for the First PR

- Multi-athlete capacity or coach/team views.
- Activity uploads or edits in Strava.
- Activity streams, GPS/map data, routes, segments, photos, or social data.
- Scheduled polling or Webhooks. User-triggered sync are sufficient.
- Cross-provider destructive deduplication between Intervals.icu and Strava.
- Using a static dashboard access token. Strava access tokens expire after approximately six hours; even Single Player Mode must use the refresh-token flow.

---

## Proposed File Structure

### New files

- `backend/models/strava.py`
  - OAuth state, athlete, token, connection, status, sync, rate-limit, and webhook models.
- `backend/repos/strava_repo.py`
  - Connection/state persistence, token-refresh lease operations, webhook queue operations, and deletion orchestration.
- `backend/services/strava.py`
  - OAuth, refresh, API requests, pagination, rate-limit parsing, mapping, remote revocation, and webhook handling.
- `components/profile/provider-connection-section.tsx`
  - Shared presentational component for Intervals.icu and Strava connection panels.
- `tests/python/test_strava_oauth.py`
- `tests/python/test_strava_repo.py`
- `tests/python/test_strava_sync.py`
- `tests/python/test_strava_webhooks.py`
- `tests/web/profile-strava.test.tsx`
- `public/brand/strava/connect-with-strava.svg`
  - Unmodified official asset downloaded from Strava's brand package.
- Supabase migrations, named with the next available timestamps at implementation time:
  - `<timestamp>_strava_connections_and_oauth_states.sql`
  - `<timestamp>_strava_activity_source_and_idempotency.sql`
  - `<timestamp>_strava_webhook_queue_and_lifecycle_rpcs.sql`

### Expected Modified files

- `.env.example`
- `.env.bootstrap.example`
- `api/index.py`
- `app/privacy/page.tsx`
- `app/profile/page.tsx`
- `backend/config.py`
- `backend/repos/__init__.py`
- `backend/repos/supabase_repo.py`
- `docs/supabase-migration-history.md`
- `lib/coach-api.ts`
- `lib/schemas.ts`
- `lib/types.ts`
- `scripts/bootstrap/config.py`
- `scripts/bootstrap/main.py`
- `scripts/bootstrap/vercel_client.py`
- `tests/python/test_bootstrap.py`
- `tests/python/test_supabase_db.py`
- `tests/python/test_supabase_repo.py`
- `tests/web/coach-api.test.ts`
- `tests/web/vercel-config.test.ts`
- `vercel.json`
- `api/index.py` recent-activity and compliance endpoints
- `lib/agent/coach-tools.ts`
- `lib/agent/orchestrator.ts`
- `lib/agent/supabase-agent-session.ts`
- activity/planning provenance models and tests

---

## Task 1: Freeze the Data Contract and Threat Model

**Documentation/tests first; no provider calls yet.**

- [ ] Write a short implementation note in the PR describing:
  - single authorized athlete;
  - requested scope;
  - imported field allowlist;
  - retention period;
  - AI-processing state;
  - deletion behavior;
  - subprocessors;
  - webhook and rate-limit strategy.
- [ ] Choose the least-privileged scope:
  - prefer `activity:read` if Only Me activities are not required;
  - use `activity:read_all` only if approved and required;
  - never request `activity:write`, `profile:write`, or unrelated scopes in v1.
- [ ] Define the exact normalized field allowlist:
  - Strava activity ID and athlete ID for provenance;
  - `sport_type` with deprecated `type` only as fallback;
  - `start_date` and `start_date_local`;
  - moving/elapsed time;
  - distance and total elevation gain;
  - average/max heart rate;
  - average/weighted power;
  - average cadence;
  - optional activity name and device name only if approved.
- [ ] Explicitly exclude map/polyline, coordinates, location, photos, social counts, segment efforts, and external upload identifiers.
- [ ] Define provider-owned versus athlete-owned canonical fields. Provider updates must not overwrite athlete notes, fueling notes, local RPE, plan links, or other Coach Arden annotations.
- [ ] Decide retention/deletion treatment for derived records before enabling AI:
  - if derived outputs may remain, record that authorization rule;
  - otherwise add source lineage and delete affected assistant messages, model state, plans, summaries, and analytics;
  - if exact lineage is unavailable, use conservative user-visible deletion of all artifacts created from the first AI-enabled Strava use through disconnect.
- [ ] Threat-model OAuth login CSRF, stolen refresh tokens, replayed state, concurrent refresh, webhook spoofing/replay, cross-user access, logs/telemetry leaks, and malicious/malformed activity payloads.

**Review checkpoint:** Security and product reviewers sign off on the data-flow diagram before migrations are written.

---

## Task 2: Add Configuration and Deployment Plumbing

**Files:** `.env.example`, `.env.bootstrap.example`, `backend/config.py`, bootstrap scripts, `vercel.json`, related tests.

- [ ] Add fail-closed settings:

```text
STRAVA_CLIENT_ID=
STRAVA_CLIENT_SECRET=
STRAVA_TOKEN_ENCRYPTION_SECRET=
STRAVA_WEBHOOK_VERIFY_TOKEN=
STRAVA_AUTHORIZATION_VERSION=
STRAVA_INTEGRATION_ENABLED=false
STRAVA_RETENTION_DAYS=
```

- [ ] Treat client secret, token encryption secret, and webhook verification token as sensitive in `scripts/bootstrap/vercel_client.py`.
- [ ] Add bootstrap settings, conditional Vercel provisioning, and masked configuration summaries.
- [ ] Add `/api/strava/:path*` rewrite to `vercel.json` and update `tests/web/vercel-config.test.ts`.
- [ ] Validate configuration combinations at service use time:
  - disabled integration returns a bounded 503;
  - enabled integration requires all OAuth/token settings and an authorization version;
- [ ] Test through OAuth mocks or a real local callback registered with Strava.

**Logical commit:** `chore: configure gated Strava integration`

---

## Task 3: Add Database Schema and Atomic Lifecycle RPCs

**Files:** new Supabase migrations, `docs/supabase-migration-history.md`, DB tests.

### Connection table

- [ ] Create `strava_connections` with:
  - UUID primary key;
  - Coach Arden `user_id` foreign key with cascade on account deletion;
  - Strava athlete ID stored losslessly (text or bigint with validated serialization);
  - optional approved display name;
  - granted scopes;
  - encrypted access and refresh tokens;
  - token type and `expires_at`;
  - authorization version and athlete-consent timestamp;
  - `connected_at`, `updated_at`, `last_sync_at`, `disconnect_requested_at`;
  - refresh lease owner/expiry or equivalent serialization fields;
  - no plaintext token or authorization code columns.
- [ ] Add one-active-connection-per-user and athlete-capacity constraints appropriate to Single Player Mode.
- [ ] Enable RLS, revoke public/anon/authenticated access, and grant only `service_role`.

### One-time OAuth state

- [ ] Create a short-lived `strava_oauth_states` table containing only hashed opaque state, user ID, browser-binding hash, expiry, and consumed timestamp.
- [ ] Add an atomic `consume_strava_oauth_state` RPC that rejects missing, expired, consumed, or browser-mismatched state.
- [ ] Add a cleanup path for expired state rows.

### Atomic connection/token lifecycle

- [ ] Add `replace_strava_connection` using a per-user advisory transaction lock, following the repaired Intervals connection pattern.
- [ ] Add refresh lease RPCs:
  - acquire only when refresh is needed and no live lease exists;
  - persist rotated access **and refresh** tokens with compare-and-swap generation/version checking;
  - release/expire a failed lease safely;
  - prevent a stale response from overwriting a newer rotated refresh token.
- [ ] Add a disconnect-state RPC that blocks future reads immediately while preserving encrypted credentials only long enough to retry remote revocation.
- [ ] Add a final purge RPC/transaction that removes the connection, imported Strava data, pending provider events, and authorized derivatives according to Task 0.

### Activity source and idempotency

- [ ] Extend `activities_source_check` with `strava_sync` using the repository's `NOT VALID` then validate migration pattern.
- [ ] Add a Strava-only generated source-key column and unique constraint, analogous to but independent from `intervals_source_file_key`.
- [ ] Use a stable key such as `strava:{athlete_id}:{activity_id}`.
- [ ] Add an RPC or constrained repository upsert that:
  - inserts a new canonical activity;
  - updates only provider-owned fields on an existing Strava activity;
  - preserves local annotations and plan links;
  - reports inserted, updated, or unchanged;
  - is safe under overlapping sync and webhook processing.
- [ ] Add a deletion RPC that removes the Strava activity and relies on existing `ON DELETE SET NULL` links where appropriate, then recalculates any allowed derived state.

### Webhook queue

- [ ] Create `strava_webhook_events` with an idempotency key derived from subscription/object/aspect/event time, minimal payload, status, attempts, and timestamps.
- [ ] Investigate and recommend queue technology that aligns with our current (low usage, no cost) model of infrastructure, preferring already used vendors where they have a solution (e.g. Cloudflare, Supabase)
- [ ] Do not retain processed webhook payloads longer than needed.
- [ ] Add claim/complete/fail RPCs with leases so concurrent processors cannot duplicate work.

- [ ] Update `docs/supabase-migration-history.md` in the same commit.
- [ ] Add DB-marked tests for RLS/grants, atomic state consumption, concurrent connection replacement, refresh leasing/CAS, activity upsert preservation, webhook dedupe, and purge behavior.

**Logical commit:** `feat: add Strava connection and sync schema`

---

## Task 4: Implement Strava Models and Repository

**Files:** `backend/models/strava.py`, `backend/repos/strava_repo.py`, `backend/repos/__init__.py`, repository tests.

- [ ] Model initial token responses separately from refresh responses because refresh responses do not include the athlete object or scope consistently.
- [ ] Validate:
  - athlete ID is a positive integer serialized losslessly;
  - access/refresh tokens and token type are non-empty;
  - `expires_at` is a valid epoch value;
  - scopes are normalized from Strava's space/comma-delimited responses;
  - required activity read scope was actually granted.
- [ ] Define status without secrets:
  - `connected`/`disconnect_pending`;
  - athlete ID/display name;
  - scopes;
  - connected and last-sync timestamps;
  - authorization version;
  - no token, refresh generation, or ciphertext fields.
- [ ] Implement repository methods for state creation/consumption, active connection read, atomic replacement, refresh leasing/rotation, disconnect state, purge, and webhook queue.
- [ ] Parse PostgREST's single-composite RPC responses correctly; test dict, null, list, and scalar failure shapes.
- [ ] Keep repository errors provider-specific and never include row payloads or ciphertext in messages.

**Logical commit:** `feat: persist Strava OAuth lifecycle`

---

## Task 5: Implement OAuth, Token Refresh, and Remote Revocation

**Files:** `backend/services/strava.py`, `api/index.py`, OAuth tests.

### Authorization

- [ ] `POST /api/strava/authorize` requires Coach Arden bearer authentication and the integration feature gate.
- [ ] Generate a cryptographically random opaque state, store only its hash, and bind it to both the user and an HttpOnly, Secure, SameSite=Lax browser cookie.
- [ ] Build `https://www.strava.com/oauth/authorize` with:
  - `client_id`;
  - exact registered `redirect_uri`;
  - `response_type=code`;
  - `approval_prompt=auto` or `force` only when reconnecting intentionally;
  - least-privileged scope;
  - opaque one-time state.
- [ ] The browser receives only the Strava authorization URL.

### Callback and exchange

- [ ] `GET /api/strava/callback` accepts Strava's `code`, `scope`, `state`, and denial error parameters.
- [ ] Consume state exactly once and verify the browser-binding cookie before exchanging the code.
- [ ] Exchange at Strava's documented token endpoint using form-encoded fields, not the Intervals JSON request:
  - `client_id`;
  - `client_secret`;
  - `code`;
  - `grant_type=authorization_code`.
- [ ] Validate the returned athlete and granted scopes. If the athlete omitted the required activity scope, do not persist the connection; redirect with a bounded scope error.
- [ ] Encrypt both access and refresh tokens with a Strava-specific encryption secret.
- [ ] Store the connection atomically, clear the state cookie, and redirect to `/profile?strava=connected`.
- [ ] Never log codes, state values, token responses, or ciphertext.

### Refresh

- [ ] Before every Strava API request, refresh when expiry is at or below Strava's recommended one-hour threshold.
- [ ] Acquire the database refresh lease before making the network call; requests that lose the lease should reload/poll briefly rather than use a stale refresh token.
- [ ] Send form-encoded `grant_type=refresh_token`, client credentials, and the latest refresh token.
- [ ] Encrypt and atomically persist both returned tokens and expiry. Always treat the returned refresh token as authoritative, even when it differs.
- [ ] On invalid grant/401, mark the connection reconnect-required without exposing upstream payloads.
- [ ] Bound retries to transport errors only; do not repeatedly retry rejected refresh tokens.

### Disconnect

- [ ] `DELETE /api/strava/connection` first marks the connection blocked so no new sync or AI read can start.
- [ ] Revoke the grant through the current recommended `POST https://www.strava.com/oauth/revoke` using HTTP Basic app credentials and form-encoded token.
- [ ] Treat a 200 response as idempotent success.
- [ ] On retryable remote failure, retain only encrypted credentials needed for retry, return `disconnect_pending`, and keep all reads blocked.
- [ ] After successful remote revocation, run the approved purge and return deletion counts plus written on-screen confirmation.
- [ ] Handle a grant already revoked at Strava as successful local deletion.

### Error mapping

- [ ] Configuration -> 503.
- [ ] Not connected/reconnect required -> 409.
- [ ] Invalid client request/scope -> 400 or 422.
- [ ] Strava authentication failure -> 409 reconnect-required.
- [ ] Strava transient failure -> 502/503 with a bounded generic message.
- [ ] Rate limit -> 429 with bounded retry guidance.
- [ ] Let `PostgRESTAPIError` reach the centralized handler unless an endpoint intentionally has a documented different contract.

**Focused verification:**

```bash
uv run pytest tests/python/test_strava_oauth.py tests/python/test_strava_repo.py
uv run ruff check backend/services/strava.py api/index.py
uv run ruff format --check backend/services/strava.py tests/python/test_strava_oauth.py
```

**Logical commit:** `feat: connect and refresh Strava OAuth`

---

## Task 6: Implement Bounded Activity Sync

**Files:** `backend/services/strava.py`, `backend/repos/supabase_repo.py`, `api/index.py`, sync/repository tests.

### API fetch

- [ ] Add `POST /api/strava/sync` with a validated day window capped by the authorization from Task 0.
- [ ] Resolve/refresh authorization before fetching.
- [ ] Call `GET https://www.strava.com/api/v3/athlete/activities` with:
  - bearer token header;
  - `after` and `before` epoch boundaries;
  - explicit `page`;
  - `per_page=100`;
  - an upper page/request bound.
- [ ] Paginate until the response has fewer than `per_page` items, while respecting rate limits.
- [ ] Use a small approved overlap window on repeated syncs so late updates are repaired by idempotent upsert.
- [ ] Reject malformed non-list payloads and skip malformed individual records without aborting valid records.

### Rate limits

- [ ] Parse and validate `X-RateLimit-Limit`, `X-RateLimit-Usage`, `X-ReadRateLimit-Limit`, and `X-ReadRateLimit-Usage`.
- [ ] Log bounded numeric usage without tokens or full URLs.
- [ ] Stop pagination before exhausting a configured safety reserve.
- [ ] Translate 429 into a user-actionable response based on the next natural quarter-hour or daily UTC reset; do not hammer retries.
- [ ] Store only the minimal last-known usage needed for status/operations if approved.

### Mapping

- [ ] Prefer `sport_type` and use a comprehensive Strava-to-canonical map for
      cycling, running, swimming, rowing, hiking, walking, strength, yoga, and
      general fallback. Consider adding additional types as appropriate based on
      Strava types where they could be useful in the future.
- [ ] Derive `activity_date` from `start_date_local`; derive absolute `started_at` only from `start_date`.
- [ ] Map summary metrics without semantic invention:
  - moving time, falling back to elapsed time;
  - distance;
  - elevation gain;
  - average/max HR;
  - average watts;
  - `weighted_average_watts` to normalized-power field with provenance;
  - average cadence.
- [ ] Leave TSS, intensity factor, and zones unset because Strava summary data
      does not provide the athlete-specific threshold context required to
      calculate them safely.
- [ ] Leave RPE and notes unset unless they are semantically valid from this
      source or are sourced from a different semantically valid source.
- [ ] Build `activity_summary` using the existing canonical helper.
- [ ] Store an allowlisted `strava_summary` provenance object.
- [ ] Set `source="strava_sync"` and stable `source_file_key`.

### Persistence and matching

- [ ] Return inserted/updated/unchanged/invalid counts.
- [ ] Make overlapping syncs race-safe through the database upsert contract.
- [ ] Preserve athlete-entered fields and existing planned-workout links on provider updates.
- [ ] Run plan matching/compliance finalization.
- [ ] Do not attempt cross-provider fuzzy deduplication against Intervals.icu
      in v1; deduplicate Strava activities only by their stable provider key.
- [ ] Document that connecting both sources can show duplicate real-world activities.

**Logical commit:** `feat: sync Strava activity`

---

## Task 7: Implement Webhook and Deletion Lifecycle

**Files:** service/repository/API, webhook tests, Vercel cron config if used.

### Subscription lifecycle

- [ ] Document that Strava allows one webhook subscription per application and manage it operationally, not per user.
- [ ] Add a script or documented curl command to create/list/delete the subscription using the correct callback URL and verify token.
- [ ] Do not expose the verify token in browser code or logs.

### Callback

- [ ] Implement unauthenticated `GET /api/strava/webhook` verification:
  - constant-time compare `hub.verify_token`;
  - echo `hub.challenge` only for valid subscribe requests;
  - reject malformed requests without leaking configuration.
- [ ] Implement `POST /api/strava/webhook`:
  - validate exact object/aspect types and scalar bounds;
  - persist a minimal idempotent event quickly;
  - return 200 within Strava's required two seconds;
  - never fetch Strava synchronously before acknowledging.
- [ ] Because Strava webhook deliveries are not documented as signed requests, treat payloads as untrusted hints. Resolve the owner against an active connection and re-fetch the authoritative resource before insert/update.

### Processor

- [ ] Process claimed events through a lease-protected worker/cron endpoint.
- [ ] Activity `create`/`update`: fetch authoritative summary and run provider-owned-field upsert.
- [ ] Activity `delete`: delete the canonical Strava activity and any derivatives required by the authorization.
- [ ] Athlete `authorized=false`: block reads immediately, purge data, and mark the connection deauthorized without requiring a usable token.
- [ ] Ignore unknown owners and unsupported event types while retaining bounded audit metadata.
- [ ] Retry transient failures with capped exponential backoff; dead-letter after a bounded attempt count and alert.
- [ ] Purge processed event payloads on the approved schedule.
- [ ] Ensure manual sync and webhook processing use a shared rate-limit budget/safety reserve.
- [ ] Investigate and recommend a queue based solution that aligns with our current (low usage, no cost) model of infrastructure, preferring already used vendors where they have a solution (e.g. Cloudflare, Supabase)

### Retention and reconciliation

- [ ] Add a daily purge/reconciliation job if the authorization has a finite retention window.
- [ ] Delete activities older than the permitted retention period and any prohibited derived data.
- [ ] Re-fetch an overlap window to reflect privacy changes or missed webhook deliveries.

**Logical commit:** `feat: process Strava lifecycle webhooks`

---

## Task 8: do the needful for data

**Files:** API activity reads, agent tool paths, durable session/provenance files as needed, tests, and `docs/COMPACTION_DESIGN.md` if durable-session behavior changes.

- [ ] Ensure strava sync data is used for each purpose:
  - athlete calendar display;
  - compliance/matching analytics;
  - threshold recalibration;
  - lead-agent tools;
  - specialist context;
  - durable model state;
  - logs/observability.
- [ ] Add tests proving Strava records are included only when athlete consent is present.
- [ ] Add provenance to the following artifacts:
  - tool-run source set;
  - assistant message metadata;
  - plan generation context;
  - threshold candidate provenance;
  - model-state compaction metadata.
- [ ] If changing `lib/agent/supabase-agent-session.ts`, `durable-compaction-session.ts`, `responses-item-shapes.ts`, or orchestrator behavior, update `docs/COMPACTION_DESIGN.md` in the same change.
- [ ] Implement the approved disconnect deletion rule for derived artifacts. Prefer exact provenance; use conservative deletion when lineage is ambiguous.

**Logical commit:** `feat: Strava data integrations`

---

## Task 9: Add Typed Browser API Contracts

**Files:** `lib/types.ts`, `lib/schemas.ts`, `lib/coach-api.ts`, `tests/web/coach-api.test.ts`.

- [ ] Add `StravaConnectionStatus` with connected/disconnect-pending state, athlete identity, scopes, timestamps, authorization.
- [ ] Add Zod schemas for status, authorize response, sync request, sync response, and disconnect/purge confirmation.
- [ ] Keep sync day validation aligned exactly with the backend/authorization maximum.
- [ ] Add browser helpers:
  - `loadStravaStatus`;
  - `startStravaAuthorization`;
  - `syncStrava`;
  - `disconnectStrava`;
  - optional `exportStravaData` if required by the authorization.
- [ ] Use `authorizedFetch` for protected endpoints; webhook routes remain server-to-server only.
- [ ] Test paths, methods, authorization headers, request bodies, valid parsing, malformed responses, and pre-fetch validation.

---

## Task 10: Add `/profile` Strava Section and Consent UX

**Files:** shared provider component, `app/profile/page.tsx`, CSS if needed, official Strava asset, component tests.

- [ ] Extract the repeated connection panel presentation from `app/profile/page.tsx` into `components/profile/provider-connection-section.tsx` without changing Intervals behavior.
- [ ] Keep provider state/actions/errors independent so an Intervals error does not disable Strava and vice versa.
- [ ] Add a **Strava** section adjacent to Intervals.icu with:
  - approved disclosure of collected fields and purpose;
  - retention and deletion summary;
  - link to privacy policy;
  - official unmodified **Connect with Strava** button asset;
  - connected athlete/scopes/last sync;
  - **Sync now** and **Disconnect** actions;
  - pending disconnect/reconnect-required states.
- [ ] Follow Strava brand guidance: correct button asset/link, no implied endorsement, no modified Strava mark, and factual plain-text references.
- [ ] If imported activity data is linked back, use the exact “View on Strava” treatment from the brand guidance.
- [ ] Preserve callback query cleanup while retaining unrelated query parameters and fragments, matching Intervals behavior.
- [ ] On sync, display inserted, updated, unchanged, and invalid counts plus bounded rate-limit guidance.
- [ ] On disconnect, show written confirmation that remote access was revoked and the approved data categories were deleted; show a retryable pending state if remote revocation is unavailable.
- [ ] Ensure status sections still render if fitness metrics fail to load.
- [ ] Ensure loading and action states are accessible and buttons cannot be double-submitted.

### Component tests

- [ ] Disconnected state and connect action.
- [ ] Authorization configuration/scope error.
- [ ] Connected athlete, scopes, and last-sync display.
- [ ] Sync success with inserted/updated/unchanged counts.
- [ ] Sync failure re-enables controls.
- [ ] 429/rate-limit guidance.
- [ ] Disconnect pending and completed deletion confirmation.
- [ ] Callback success/error notice and URL cleanup.
- [ ] Intervals and Strava actions remain independent.
- [ ] Profile connection sections remain available when metrics fail.

**Logical commit:** `feat: manage Strava sync from profile`

---

## Task 11: Update Privacy, Export, Support, and Operational Documentation

**Files:** `app/privacy/page.tsx`, deployment docs or PR runbook, tests as appropriate.

- [ ] Update the privacy policy's date and add a distinct Strava section covering:
  - exact data fields;
  - OAuth and webhook collection methods;
  - permitted coaching/AI purpose;
  - subprocessors;
  - retention period;
  - no sale or cross-user display;
  - withdrawal/disconnect;
  - export/access request;
  - deletion request and confirmation;
  - Strava usage monitoring language if required.
- [ ] Update user data access/export (.jsonl) path if the approved policy requires one to include in the export Strava user's allowed Strava records and never tokens/ciphertext.
- [ ] Document support contact and deletion request procedure.
- [ ] Add an operator runbook for:
  - registering callback domain/URL;
  - creating and verifying the webhook subscription;
  - rotating app/client/encryption/verify secrets;
  - responding to a token leak within the required notice period;
  - replaying/dead-lettering webhook events;
  - completing/certifying deletion;
  - disabling import or AI processing independently;
  - monitoring athlete-capacity and rate-limit dashboards.
- [ ] Document that current issue #340 reports one-athlete capacity, 100 read requests/15 minutes, 1,000 read requests/day, 200 overall requests/15 minutes, and 2,000 overall requests/day; the runtime must still trust current response headers/dashboard values.

**Logical commit:** `docs: document Strava privacy and operations`

---

## Task 12: End-to-End and Regression Testing

### Python unit/API coverage

- [ ] OAuth URL exact parameters and least scope.
- [ ] One-time state success, expiry, replay, wrong browser, and wrong user.
- [ ] Form-encoded exchange and refresh contracts.
- [ ] Missing/reduced scopes.
- [ ] Encryption round trip and no plaintext in serialized records/logs.
- [ ] Refresh at >1 hour, <=1 hour, expired, concurrent requests, rotated token, stale CAS, and failure release.
- [ ] Remote revocation success, already revoked, transient pending, and final purge.
- [ ] Pagination boundaries, malformed page, max pages, and overlap.
- [ ] Header parsing and near-limit/429 behavior.
- [ ] Complete sport/metric/date mapper table.
- [ ] Insert/update/unchanged/invalid counts and overlapping sync.
- [ ] Provider updates preserve athlete-authored fields and plan links.
- [ ] Webhook verification, malformed payload, duplicate delivery, unknown owner, create/update/delete, and deauthorization.
- [ ] Purge removes every authorized category and leaves unrelated Intervals/manual data untouched.

### Database integration coverage

- [ ] Migration applies from clean reset.
- [ ] Service-role-only grants and RLS.
- [ ] Concurrent connection replacement leaves one active connection.
- [ ] Concurrent refresh lease permits one upstream refresh owner.
- [ ] Stale token rotation cannot replace a newer generation.
- [ ] Concurrent activity upsert is idempotent.
- [ ] Webhook event uniqueness and worker leases.
- [ ] Purge transaction and foreign-key cleanup.

### Web coverage

- [ ] Zod/browser helper tests.
- [ ] Profile component interaction tests.
- [ ] Existing Intervals profile tests remain unchanged in behavior.
- [ ] Playwright flow using mocked Strava backend endpoints for connect callback, status, sync, and disconnect.
- [ ] Calendar regression if approved Strava activities are displayed there.
- [ ] Accessibility checks for status messages, button names, and disabled states.

---

## Task 13: Full Validation and Code Review

- [ ] Run formatting and static checks:

```bash
bun run lint
bun run typecheck
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run vulture
```

- [ ] Run all unit/integration suites:

```bash
bun run test
uv run pytest
bun run db:reset
RUN_DB_TESTS=1 uv run pytest -m db tests/python/
bun run test:ui
```

- [ ] Run the repository's remaining pre-push checks, including cpd and knip through the normal hook. Do not bypass hooks.
- [ ] Perform a focused security review:
  - no secrets/tokens/codes/state in responses, logs, Sentry, fixtures, or snapshots;
  - callback and webhook routes have the intended authentication model;
  - state is one-time and browser-bound;
  - refresh tokens rotate atomically;
  - all reads are user-scoped;
  - disconnect blocks processing immediately;
  - deletion is complete and test-proven.
- [ ] Review mapper semantics field by field; specifically reject fake TSS/IF/RPE and deprecated `type` precedence.
- [ ] Review rate-limit request counts for worst-case initial sync and webhook bursts.
- [ ] Review UI against the then-current Strava brand package.
- [ ] Ask a second agent/reviewer to inspect the diff specifically for OAuth, token rotation, deletion, and migration safety.
- [ ] Resolve every finding or document why it is not applicable before requesting non-draft review.

---

## Logical Commit Plan

Keep tests with each implementation chunk.
Do not create migration-only commits without updating `docs/supabase-migration-history.md`. Do not defer core behavior tests to the final test commit.

---

## Draft PR and Rollout Plan

- [ ] Push the feature branch only after local hooks pass.
- [ ] Open a draft PR linked to #340 with:
  - authorization reference/version but no confidential attachment;
  - scope and data-field allowlist;
  - architecture and deletion summary;
  - explicit non-goals;
  - migration order;
  - test evidence;
  - screenshots of the profile connection states;
  - current policy/brand review date;
  - rollout and rollback steps.
- [ ] Keep all production feature flags false when the code first lands.
- [ ] Apply migrations before deploying application code that calls new RPCs.
- [ ] For the shipped manual-sync release, configure the callback domain, OAuth
      credentials, and encryption secret. Authorization version is optional;
      webhook and retention configuration remain deferred.
- [ ] Defer preview webhook-subscription setup until webhook processing is
      separately delivered.
- [ ] Smoke-test with the sole authorized athlete:
  - connect;
  - verify returned scope;
  - force token refresh;
  - sync a small window twice;
  - defer update/delete webhook-reconciliation testing until webhook processing
    is delivered;
  - confirm rate-limit telemetry;
  - disconnect and verify remote grant plus local data deletion.
- [ ] Inspect Supabase directly to confirm no plaintext token and no excluded upstream fields.
- [ ] Inspect agent/tool traces to prove Strava source exclusion while AI processing is off.
- [ ] Enable `STRAVA_INTEGRATION_ENABLED` in preview only.
- [ ] Repeat callback registration and manual-sync smoke tests for production;
      defer webhook registration.
- [ ] Monitor 401/429 rates, refresh failures, sync counts, and
      disconnect-pending records. Defer webhook lag/dead-letter monitoring.

### Rollback

- Turn `STRAVA_INTEGRATION_ENABLED=false` to stop new connections and pulls.
  Webhook deauthorization/deletion handling is deferred and is not part of this
  rollback path.
- Do not create or revoke a webhook subscription for this release; manage that
  lifecycle only with a future webhook delivery.
- Keep additive migrations in place unless a separately reviewed rollback migration is required; do not manually delete migration history.

---

## Final Review Notes

- Intervals.icu provides a useful UI and endpoint pattern, but Strava is not a protocol-level copy. The critical differences are rotating refresh tokens, granted-scope verification, pagination, rate-limit headers, webhook lifecycle, remote revocation, update/delete semantics, and authorization restrictions on AI/retention.
- Single Player Mode affects athlete capacity, not data-use permissions.
- The safest first release imports summary fields only and avoids GPS/streams entirely.
- No implementation is complete until disconnect and webhook deauthorization demonstrably delete or retain every direct and derived data category exactly as the approved authorization requires.
