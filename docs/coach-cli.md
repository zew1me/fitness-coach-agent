# Coach CLI

`coach` is a local command-line client for the app's HTTP API (issue #568, phases 1-2). It lets a
logged-in athlete, or a coding agent acting for them (Claude Code, Codex, pi), run the same
operations the coach tools run, push Garmin wellness data, and upload activity files.

It talks **only** to the app at a base URL. It never holds, needs, or prints Supabase or R2
credentials or URLs; storage uploads go through the app's proxy endpoint.

## Install and run

The CLI is a uv workspace package (`tools/coach-cli`, distribution `fitness-coach-cli`):

```bash
uv run --package fitness-coach-cli coach --help
# or install the command for your user:
uv tool install ./tools/coach-cli
```

Global options (before the subcommand):

| Option               | Env                      | Meaning                                                              |
| -------------------- | ------------------------ | -------------------------------------------------------------------- |
| `--base-url URL`     | `COACH_BASE_URL`         | Deployment to talk to. Required; there is deliberately no default.   |
| `--json`             |                          | Compact machine-readable output on stdout; errors as JSON on stderr. |
| `--credentials-file` | `COACH_CREDENTIALS_FILE` | Default `~/.config/fitness-coach/credentials.json`.                  |

Remote base URLs must be `https`. Failures exit non-zero; with `--json` stderr is
`{"error": "...", "status": 409, "detail": ...}`.

## Login

### Device login (default)

`coach login` with no `--email` runs the OAuth device flow (RFC 8628). It works from a remote or
headless shell: the CLI prints a short code and a URL, you approve it in any browser where you are
(or become, via the normal emailed-code web login) signed in, and the CLI stores the credentials.

```bash
export COACH_BASE_URL=https://my-preview.vercel.app
coach login [--no-browser] [--yes]
# Open https://my-preview.vercel.app/device and enter code BCDF-GHJK
# Or open: https://my-preview.vercel.app/api/oauth/device?user_code=BCDFGHJK
# Waiting for approval in the browser (Ctrl-C to cancel)...
```

- The CLI tries to open the complete URL in a browser unless `--no-browser` is given, stdout is not
  a terminal, or (on Linux) neither `$DISPLAY` nor `$WAYLAND_DISPLAY` is set. A browser that fails
  to open never fails the login.
- Check that the code on the page matches the one in your terminal before approving.
- It polls the token endpoint every `interval` seconds (5s by default), adds 5s whenever the server
  answers `slow_down`, and gives up after `expires_in` (10 minutes). Outcomes: logged in, `access_denied`
  (you clicked Deny), or `expired` (code expired, unknown, or already used; run `coach login` again).

**Agent harnesses (Claude Code, Codex, pi):** run `coach --json login`. Before polling it prints one
JSON line on stdout, then blocks until the human approves and prints the result line:

```json
{"verification_uri":"https://my-preview.vercel.app/device","verification_uri_complete":"https://my-preview.vercel.app/api/oauth/device?user_code=BCDFGHJK","user_code":"BCDF-GHJK","expires_in":600}
{"status":"logged_in","user_id":"...","base_url":"https://my-preview.vercel.app"}
```

Relay `user_code` and `verification_uri` (or the complete URL) to the human, then wait for the
command to finish. Run it in the background or with a long timeout, since it blocks for up to
`expires_in` seconds. Do not read the credentials file.

### Emailed-code login (`--email`, two steps)

The alternative for when a browser approval is not possible. It is split so a non-interactive agent
can run step 1, ask the human for the emailed code, then run step 2.

```bash
coach login --email me@example.com [--invite-code CODE]   # step 1: emails a 6-digit code
coach login --email me@example.com --code 123456          # step 2: verifies, stores credentials
```

- New accounts need an invite code; without one step 1 fails with `invite_required`.
- If `--code` is omitted and stdin is a terminal, the CLI prompts for the code after sending.
- Too many wrong codes fail with `rate_limited` (HTTP 429); wait and retry.
- `--code` and `--invite-code` are only valid together with `--email`.

### Both paths

- **Production guard:** unless the base URL is `localhost`, `127.0.0.1`, or a `*.vercel.app`
  preview, login requires `--yes`, checked before any request is sent. This is only a speed bump
  against pointing an agent at real data by accident.
- `coach whoami` shows the user id, client id (`fitness-coach-cli`) and scopes.
- `coach logout` revokes the refresh token and the access token (which revokes the whole grant
  server-side), then deletes the local credentials (even if the server is unreachable; it then says
  revocation failed).

Access tokens are refreshed automatically: proactively when within 60 seconds of expiry, and once
after a 401. Rotated tokens are persisted. If refresh fails the command exits non-zero and asks you
to run `coach login` again.

## Commands

```text
coach tools list                                   # known /api/engine operations
coach call <operation> [--data JSON|@file|-]       # POST /api/engine/<operation>; body defaults to {}
coach wellness push [FILE|-]                       # sidecar JSON -> save-recovery-data
coach activities upload FILE...                    # .fit / .gpx / .tcx
```

```bash
coach call get-athlete-summary
coach call get-recent-activities --data '{"limit": 5}'
coach call update-goals --data @goals.json
echo '{"plan_id": "...", "reason": "sick"}' | coach call adjust-plan --data -
```

`coach tools list` is a static catalogue; the server is the source of truth, so `call` accepts any
well-formed operation name (lowercase letters, digits, hyphens).

### Garmin wellness pipe

The CLI does not import the Garmin sidecar (and so does not depend on `garminconnect`). They compose
through a pipe; see [garmin-wellness.md](garmin-wellness.md):

```bash
uv run --package fitness-coach-garmin-sidecar garmin-sidecar wellness export \
  2026-08-09 2026-08-15 --format json \
  | uv run --package fitness-coach-cli coach wellness push -
```

Each sidecar row becomes a `save-recovery-data` entry with `source: "garmin"`. The sidecar's field
names already match the recovery log (`log_date`, `sleep_duration_hours`, `sleep_score`,
`sleep_consistency_pct`, `hrv_ms`, `resting_hr_bpm`, `body_battery`, `stress_score`); integer
fields are rounded. Unknown or non-numeric fields are dropped with a warning on stderr; days with no
measurements are skipped. Re-pushing a day updates that day's row.

### Activity upload

For each file the CLI calls `POST /api/chat/attachments/presign`, uploads the bytes through
`POST /api/chat/attachments/upload` (the app proxies to storage), then calls
`POST /api/engine/process-uploaded-file`. One outcome line is printed per file:

```text
saved activity ride.fit
course         route.gpx        # a route you plan to do: analysed, nothing saved
failed         notes.txt: unsupported_file: ...
```

It continues past failures and exits 1 if any file failed. Uploading the same file twice is not
deduplicated yet (issue #390).

## Security notes

- Credentials live in `credentials.json`: directory mode `0700`, file mode `0600`, written atomically,
  keyed by normalised base URL so preview and production never share tokens. Tokens are never
  printed. Treat the file like an SSH key; anyone who can read it can act as you until it is revoked.
- Revoke with `coach logout`. The CLI's grant is the OAuth client `fitness-coach-cli`; revoking it
  invalidates the CLI session only, not your browser login.
- The CLI holds no Supabase or R2 keys and needs none. Do not give them to an agent.
- Wellness and activity data is sensitive: avoid shell redirection into committed files.
- Agents should run `coach` with `--json`, never log the credentials file, and relay the device code to the
  human (or ask for the emailed code with `--email`) rather than reading mail themselves.
