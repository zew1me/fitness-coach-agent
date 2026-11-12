# Coach CLI: a hosted athlete client (#568)

`tools/coach-cli` is a uv workspace package (`fitness-coach-cli`, command `coach`).
It works on macOS/Linux and calls **only the chosen deployment's HTTP API**. It does not
need, store, or directly use any Supabase URL/key or R2 credential. The Garmin sidecar stays a
separate tool with its own Garmin credentials.

## Setup and explicit targets

From the repository root:

```sh
uv sync --all-groups
uv run --package fitness-coach-cli coach --help
uv run coach --base-url http://localhost:3000 login
uv run coach --base-url http://localhost:3000 whoami
uv run coach --base-url http://localhost:3000 call get-athlete-summary
```

The deployment needs this branch's backend `GET /api/auth/whoami`, exact Vercel rewrite,
and bearer-authenticated Next.js chat route. Local development uses `bun run dev:local`.
Existing OAuth authorization, consent, token, and revoke endpoints are reused; **no database
migration or hosted auth configuration change is needed**.

Pass global options **before the subcommand**. Alternatively set `COACH_BASE_URL` explicitly
in your shell/profile. There is no implicit local/preview/production default. The origin must
be HTTPS, except HTTP loopback development targets. Remote login, including production,
requires interactive confirmation or an explicit `login --yes`.

## Browser approval, email OTP, and callback URLs

```sh
uv run coach --base-url https://YOUR-DEPLOYMENT login
uv run coach --base-url https://YOUR-DEPLOYMENT login \
  --callback-url http://127.0.0.1:8765/callback --no-browser
```

Login prints an approval URL and normally opens it. The athlete signs in on the deployment's
existing login page (email OTP or email link), then approves the CLI on the consent page.
An already signed-in browser still sees consent. The browser returns a single-use authorization
code to the loopback listener; the CLI exchanges it with S256 PKCE through `/api/oauth/token`.
The exact callback URL participates in both authorization and code exchange.

- Default callback: `http://127.0.0.1:0/callback`; port `0` selects an available port.
- Custom callbacks accept only HTTP `127.0.0.1` or `localhost`, an optional port, and a path;
  credentials, query parameters, fragments, and non-loopback hosts are rejected.
- The server binds only to `127.0.0.1`, validates random state, ignores forged/unrelated requests,
  has bounded socket/login timeouts, and never logs request paths or codes.
- `--timeout SECONDS` defaults to 300. Denial or timeout saves no new credentials.
- `--no-browser` prints the same URL for manual opening. **It does not implement RFC 8628 device
  codes/polling.** A remote/cloud harness needs a forwarded loopback port, or must run login on
  the same machine as the browser. For SSH, forward the selected port (for example
  `ssh -L 8765:127.0.0.1:8765 HOST`) and use the fixed callback shown above on that host.

Email OTP verification stays in the browser on the deployment, not in the CLI. This avoids a
second OTP verification API, new grant type, Supabase redirect allowlist changes, and any direct
Supabase Auth traffic from the CLI. A true no-tunnel device-code flow remains future work.

## Credentials and revocation

OAuth tokens live in `~/.config/fitness-coach/credentials.json` (or under `XDG_CONFIG_HOME`),
keyed by normalized deployment origin. `--credentials PATH` selects another private store.
The directory is `0700`, files are `0600`, writes are atomic, and a separate file lock serializes
refresh-token rotation between CLI processes. These are sensitive plaintext tokens: do not copy
or commit the store. Login replaces the saved identity for that origin; targets remain isolated.

Access tokens expire after 15 minutes. The CLI refreshes near expiry or after one explicit 401;
a transport failure never automatically replays a possibly completed write. The backend checks
that the OAuth grant is active on every authenticated request. Revocation produces a re-login
hint rather than silently switching to browser cookies or another target. Each command also pins a
random login generation: a concurrent login in another terminal cannot cause a 401 retry or a later
step of a multi-request upload to run as a different athlete. Concurrent refreshes within the same login remain reusable.

```sh
uv run coach --base-url https://YOUR-DEPLOYMENT logout
```

Logout revokes the current **grant** remotely before removing that target's local credentials.
It retains credentials if remote revocation fails, so an outage is not reported as a successful
logout. If another terminal logs in while revocation is pending, its newer credentials are preserved;
the result reports `newer_login_preserved: true` rather than claiming that newer login was logged out.
An already-revoked grant may require a fresh login; manual file deletion alone does not revoke a
still-active remote grant.

## Operations and Garmin push

```sh
uv run coach tools list
uv run coach --base-url "$COACH_BASE_URL" --json call get-athlete-summary
uv run coach --base-url "$COACH_BASE_URL" call update-schedule --data @schedule.json
printf '{}' | uv run coach --base-url "$COACH_BASE_URL" call get-recent-activities --data -

uv run garmin-sidecar wellness export 2026-11-01 2026-11-10 --format json |
  uv run coach --base-url "$COACH_BASE_URL" wellness push -
uv run coach --base-url "$COACH_BASE_URL" activities upload ride.fit route.gpx activities.zip
```

`call OPERATION` POSTs a JSON object to `/api/engine/OPERATION`. The tool list describes common
operations, not a new domain implementation or exhaustive server capability registry. Wellness
push accepts a JSON entry array or `{"entries": [...]}` and preserves each entry's source.
Activity upload uses presign → backend multipart upload proxy → the existing file/zip processor;
the CLI never follows an R2 or signed upload URL. Existing course/activity classification applies.
Files must be nonempty and at most 25 MiB. Multi-file uploads stop at the first failure; earlier
successful files are not rolled back.

**Writes are real.** A valid grant currently reaches the existing bearer-authenticated engine
endpoints with the app's existing coarse OAuth scopes. There are not yet per-operation scopes,
CLI write confirmations/dry runs, or activity deduplication guarantees. In particular, generate-plan
supersedes the active plan, and replaying an activity/wellness write follows today's server behavior.
Do not blindly retry a request after a network failure; inspect current data first.

## Chat and automation output

```sh
uv run coach --base-url "$COACH_BASE_URL" chat send "How should I train today?" --stream
uv run coach --base-url "$COACH_BASE_URL" --json chat send "Review my recovery" --stream
uv run coach --base-url "$COACH_BASE_URL" chat history --limit 20
```

Chat uses the same athlete thread, persisted messages, and turn lease as the web UI. Explicit
bearer headers are verified by FastAPI; invalid headers fail closed even if a browser cookie is
also present. `--stream` prints text deltas immediately. Without it, text is collected until
completion. Global `--json` emits every AI SDK UI-message event as compact JSONL, including tool
activity; other commands emit compact JSON. Human-oriented structured results are pretty JSON.
Errors exit nonzero and emit `{"error":"coach_error","status":...,"detail":...}` to stderr.
Network errors do not echo headers, response bodies, codes, or tokens. Stream errors, cancellation,
and incomplete turns fail rather than masquerading as success; check history before retrying.

This is a shell-facing client for humans and agent harnesses, not an MCP server or Agent Skill.
Protected Vercel previews still need their usual deployment-access setup; an application login is
not a Vercel protection bypass.

## Validation

```sh
uv run pytest tools/coach-cli/tests/
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run vulture
bun run check
```

Default CLI tests use HTTP mock transports and fake approval servers, never real deployments,
email, browser launches, or Supabase credentials. Live athlete login is an interactive follow-up,
not part of the default test suite.
