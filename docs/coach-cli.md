# Coach CLI (`coach`)

A headless client for a fitness-coach deployment. A human at a terminal, or a coding-agent
harness (Claude Code, Codex, pi), signs in as an athlete with the emailed code and then calls
coach operations directly. Tracking issue: #568 (this is phase 1: auth and the CLI core).

```bash
uv run --package fitness-coach-cli coach --help
```

## Invariants

- **Every call is a signed-in athlete.** There is no shared key, service-role key or
  agent-only bypass. The athlete completes the email code step.
- **Only the deployment's API.** The CLI never holds a Supabase URL or key and never calls
  Supabase Auth, PostgREST or R2 directly. The server verifies the code.
- **Scoped, short-lived, revocable credentials.** A login is an ordinary `oauth_grants` row for
  client id `fitness-coach-cli` with a 15-minute access token and a rotating 30-day refresh
  token, the same machinery the OAuth provider uses.
- **One implementation per operation.** `coach call <op>` forwards to `POST /api/engine/<op>`,
  the endpoint the web coach's tools call. The CLI has no domain logic.
- **The target is always explicit.** Pass `--base-url` (or set `COACH_BASE_URL`) on every run.
  A production deployment, or one whose environment cannot be read, needs confirmation.

## Sign in

Signing in takes two non-interactive steps, so an agent can run step 1, ask the human for the
code, and run step 2. No browser or loopback port is needed, so it works from remote
and cloud harnesses.

```bash
coach --base-url https://<deployment> login --email me@example.com            # emails a code
coach --base-url https://<deployment> login --email me@example.com --code 123456
coach --base-url https://<deployment> whoami
```

In an interactive terminal, step 1 prompts for the code instead of exiting. A first-time
email needs `--invite-code`.

### Choosing a target

| Target                      | `--base-url`                  | Confirmation                        |
| --------------------------- | ----------------------------- | ----------------------------------- |
| Local (`bun run dev:local`) | `http://localhost:3000`       | none                                |
| Vercel preview              | `https://<branch>.vercel.app` | none if `/health` reports `preview` |
| Production                  | the production origin         | `--yes`, or an interactive prompt   |

The CLI reads the deployment's `APP_ENV` from `GET /health`. Anything not reported as
`development` or `preview` (including an unreachable or older deployment) is treated as
production and needs confirmation. Plain `http` is refused for any non-local host.

Vercel previews behind Deployment Protection need the automation bypass. Set
`VERCEL_AUTOMATION_BYPASS_SECRET` (see `.env.agent.example`). The CLI sends it only to
`*.vercel.app` hosts.

## Commands

| Command                                                      | Does                                                                    |
| ------------------------------------------------------------ | ----------------------------------------------------------------------- |
| `coach login --email E [--code C] [--invite-code I] [--yes]` | Send a code, or finish signing in                                       |
| `coach logout`                                               | Revoke this login's grant server-side and delete the local tokens       |
| `coach whoami`                                               | Show the user id, client id and scopes the server resolves the token to |
| `coach call <op> [--data JSON \| --data @file \| --data -]`  | `POST /api/engine/<op>` with a JSON object body                         |
| `coach tools list`                                           | List the operations `call` can reach (no deployment needed)             |

Global options come before the command: `--base-url`, `--json`, `--credentials-file` and
`--timeout`.

```bash
coach --base-url "$URL" --json call get-athlete-summary
coach --base-url "$URL" call get-recent-activities --data '{"days": 14}'
coach --base-url "$URL" call save-recovery-data --data @recovery.json
```

### Output and errors

Human-readable text is the default. With `--json`, results are compact JSON on stdout. Any failure exits
non-zero, and with `--json` it prints a machine-readable error on stderr:

```json
{
  "error": "session_expired",
  "status": 401,
  "detail": "…",
  "hint": "Run `coach … login …` again."
}
```

| `error`                                | Meaning                                                                                                |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `not_logged_in`                        | No stored sign-in for this `--base-url`                                                                |
| `session_expired`                      | Refresh was rejected: the grant was revoked or the refresh token expired. Sign in again                |
| `rate_limited`                         | Too many attempts; `hint` carries `Retry-After`                                                        |
| `confirmation_required`                | Production or unknown target without `--yes`                                                           |
| `invalid_base_url`, `missing_base_url` | Fix the target                                                                                         |
| `invalid_input`, `invalid_operation`   | Bad `--data` or operation name; nothing was sent                                                       |
| `deployment_protected`                 | A non-JSON 401/403 from in front of the app (e.g. Vercel Deployment Protection); not a sign-in problem |
| `http_error`                           | Any other non-2xx; `status` and `detail` come from the server                                          |
| `network_error`, `invalid_response`    | Transport failure or unexpected body                                                                   |

## Credentials

Tokens are stored in `~/.config/fitness-coach/credentials.json`, or under `$XDG_CONFIG_HOME`,
or at `--credentials-file`. The directory is `0700` and the file `0600`, matching the Garmin
sidecar's token store. Entries are keyed by the normalized base URL, so local, preview and
production sign-ins live side by side.

- The access token is refreshed before it expires and once after a `401`. Refresh tokens rotate
  on every use, so concurrent `coach` processes serialize refresh on a lock file. A process that
  waited reuses the tokens the other one saved.
- `logout` refreshes first if the access token has expired. It then revokes the grant through
  the access token, which invalidates every access token minted from it. Revoking only the
  refresh token would leave the current access token live. It also revokes the refresh token and
  then deletes the local entry, even if the server could not be reached.

## Server endpoints

| Endpoint                                  | Purpose                                                                             |
| ----------------------------------------- | ----------------------------------------------------------------------------------- |
| `POST /api/auth/request-otp` (Next.js)    | Email the code; enforces the invite code for new emails                             |
| `POST /api/auth/cli/verify-otp` (FastAPI) | `{email, token}` → `{access_token, refresh_token, expires_at, token_type, user_id}` |
| `GET /api/auth/whoami` (FastAPI)          | `{user_id, client_id, scopes}` for the bearer token                                 |
| `POST /api/oauth/token`                   | `grant_type=refresh_token`, `client_id=fitness-coach-cli`                           |
| `POST /api/oauth/revoke`                  | Revoke the grant (access token) or a refresh token                                  |
| `GET /health`                             | `{status, environment}`, used for the production check                              |

`verify-otp` calls Supabase Auth's `/auth/v1/verify` over plain HTTP from the server. It does
not use supabase-py, so the user's session never lands on a shared client. It then signs that
Supabase session out at once. The CLI only ever holds the app's own grant tokens.

- Each login creates its own grant (redirect URI `urn:fitness-coach-cli:login:<uuid>`). Logging
  out on one machine does not sign out another.
- Every rejected code returns the same `401 Invalid or expired code.`, whether the email
  exists or not.
- A malformed request returns `422` before Supabase is called.
- Supabase being unavailable returns `503`.
- Five verify attempts per email per 15 minutes; the sixth returns `429` with `Retry-After`. A
  successful sign-in clears the count. Supabase's own verify limit is per client IP, and every
  server-side call arrives from the deployment's shared egress IPs, so that limit cannot
  protect a single account.
- The rate limiter lives in process memory (`backend/services/attempt_limiter.py`). It is
  best-effort per serverless instance, like the model circuit breaker. A shared store would
  make it global.

Phase 1 tokens carry every supported scope, and endpoints do not yet enforce scopes per route.
Per-endpoint scopes, confirmation for high-impact writes, and a "connected devices" list are
phase 5 of #568.

## Development

```bash
uv run pytest tools/coach-cli/tests/               # CLI against an in-process fake deployment
uv run pytest tests/python/test_cli_auth.py        # verify-otp, whoami, refresh, revoke, isolation
uv run pytest tests/python/test_coach_cli_contract.py   # catalog ↔ engine routes, client id
```

`tests/python/test_coach_cli_contract.py` fails when an `/api/engine/*` route is added or
removed without updating `tools/coach-cli/src/fitness_coach_cli/operations.py`.
