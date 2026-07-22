# Strava Integration Runbook

Operational notes for the per-athlete Strava connection (issue #340). This first
release covers **connect / status / disconnect / manual Sync now** with
Strava-correct rotating-token refresh. Webhooks, scheduled polling, retention
jobs, cross-provider deduplication, and feeding Strava data into AI/agent paths
are explicit non-goals for this PR.

## Client-library decision (2026-07-21)

**Decision: do not add `stravalib`; keep the direct async `httpx` boundary in
`backend/services/strava.py`.** This is an intentional architecture decision,
not a temporary omission. We evaluated `stravalib` 2.5.0 (released 2026-06-01)
and will not mix it into this integration.

`stravalib` is a capable, maintained client for synchronous Python applications,
but it is a poor fit for this service:

- its transport uses synchronous `requests`, and its rate limiter calls
  `time.sleep`; invoking either in an async FastAPI handler would block the event
  loop (offloading every call to a worker would add complexity without removing
  the other mismatches);
- its automatic refresh mutates tokens held by one client instance and refreshes
  only after expiry, whereas this app refreshes one hour early, encrypts both
  rotated tokens, and compare-and-swaps them in shared persistence so concurrent
  serverless invocations cannot overwrite a newer refresh;
- this release uses only four small HTTP surfaces (authorize, token,
  revoke, and paginated activity summaries), with application-specific
  response validation, retry semantics, provenance allowlisting, and HTTP error
  contracts that a wrapper would not replace; and
- adopting it would add `requests`, `arrow`, and `pint` to the production graph
  while retaining most of the current service code around it.

Do not add `stravalib` merely for activity models, OAuth URL construction, or
pagination. Revisit this decision only if the integration expands enough to
make broad endpoint coverage material **and** the library offers a genuinely
async transport plus hooks that preserve application-owned refresh persistence
and rate-limit/error behavior. Record any reversal here before changing code.

## Data contract

- **Capacity:** one authorized athlete (Single Player Mode).
- **Scope requested:** `read,activity:read` (least privilege). `activity:read_all`
  is only used under explicit approval; write scopes are never requested.
- **Imported fields (summary only):** Strava activity id + athlete id
  (provenance), `sport_type` (deprecated `type` only as fallback),
  `start_date` / `start_date_local`, moving/elapsed time, distance, elevation
  gain, average/max HR, average watts, `weighted_average_watts` → normalized
  power, average cadence, and optional name/device name for provenance.
- **Explicitly excluded:** map/polyline, GPS coordinates, routes, segments,
  photos, social counts, upload identifiers.
- **Not fabricated:** TSS, intensity factor, and zones are left unset — Strava's
  summary provides none and the athlete's thresholds are required to derive them
  honestly.
- **Provider-owned vs athlete-owned:** provider writes only the imported summary
  metrics. Athlete notes, RPE, and fueling notes are not inferred. Strava
  imports never create planned-workout links (idempotent insert on
  `strava:{athlete_id}:{id}`).
- **AI-processing boundary:** `source='strava_sync'` data is allowed in the
  non-AI calendar and profile connection/sync flows, but is excluded from every
  activity read reachable by an agent: recent activities, compliance and plan
  matching, threshold recalibration, and load recomputation. Guessed activity
  IDs cannot bypass the boundary, legacy Strava auto-matches are neutralized in
  AI-facing plan reads, and the TypeScript tool executor rejects any response
  that still contains Strava provenance before OpenAI or durable model state can
  receive it. Consequently specialist/delegation context and AI-facing derived
  results cannot acquire Strava activity data. Keep these server and tool-output
  guards together when adding an activity-backed agent feature.

## Configuration (fail-closed)

The integration returns `503` until the enable flag and all three required
credentials are configured: `STRAVA_INTEGRATION_ENABLED=true`,
`STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, and
`STRAVA_TOKEN_ENCRYPTION_SECRET`. `STRAVA_AUTHORIZATION_VERSION` is optional;
when set, it is a coarse consent label surfaced in status.

```dotenv
STRAVA_INTEGRATION_ENABLED=false   # flip true after credentials are set
STRAVA_CLIENT_ID=
STRAVA_CLIENT_SECRET=
STRAVA_TOKEN_ENCRYPTION_SECRET=    # must differ from the Intervals secret
STRAVA_AUTHORIZATION_VERSION=      # optional consent label surfaced in status
```

Access, refresh, and encryption secrets are marked sensitive in the Vercel
bootstrap (`SENSITIVE_ENV_KEYS`).

## Registering the callback

Register only the base host/domain (for example, `coach.example.com`) as the
Authorization Callback Domain in the Strava API application—do not include a
scheme or path. The OAuth redirect URI sent by this app is separately
`<base_url>/api/strava/callback`; for local development, use callback host
`localhost` and redirect URI `http://localhost:3000/api/strava/callback`. Strava
access tokens expire ~6h, so local development uses the real OAuth + refresh
flow — there is no static dev token bypass.

## Token rotation

- Refresh runs before every API request when the token is within one hour of
  expiry.
- Both the access and refresh tokens are re-encrypted and persisted atomically
  via `rotate_strava_tokens`, which compare-and-swaps on the previously observed
  `expires_at`. A lost CAS reloads the already-rotated token instead of
  overwriting it. The returned refresh token is always treated as authoritative.
- An `invalid_grant`/401 on refresh marks the connection reconnect-required (no
  retry); the athlete reconnects from `/profile`.

## Disconnect / deletion

`DELETE /api/strava/connection`:

1. Decrypts the access token and calls `POST https://www.strava.com/oauth/revoke`
   with HTTP Basic application credentials and a form-encoded `token`. Only a
   `200` response is treated as success.
2. On an upstream failure (including `401`), credentials are retained and the
   endpoint returns `disconnect_pending: true` with reads still blocked; the
   athlete can retry.
3. On success, the connection row is revoked and all `source='strava_sync'`
   activities are purged. The response includes `deleted_activities`, shown to
   the athlete as written confirmation.

## Rate limits

Manual sync paginates `GET /athlete/activities` (`per_page=100`, capped at 10
pages) and stops on a short page. `X-RateLimit-*` / `X-ReadRateLimit-*` headers
are logged (numeric usage only, no tokens or URLs). A `429` surfaces as an HTTP
`429` with a bounded `Retry-After` pointing at the next quarter-hour reset — the
runtime trusts live headers over any documented static limits.

## Rotating secrets / responding to a leak

Rotate `STRAVA_CLIENT_SECRET` and `STRAVA_TOKEN_ENCRYPTION_SECRET` in the
environment. Rotating the encryption secret invalidates stored ciphertext, so
affected athletes must reconnect. On a suspected token leak, disable the
integration (`STRAVA_INTEGRATION_ENABLED=false`) to stop new connections/pulls,
then disconnect affected athletes to force remote revocation.

## Rollback

Set `STRAVA_INTEGRATION_ENABLED=false` to stop new connections and syncs. Keep
the additive migrations in place. (Webhook deletion handling is not part of this
release, so there is no webhook subscription to preserve.)
