# Local Garmin Connect data downloader

The Garmin sidecar is a **local, personal-use workaround** built on
[`cyberjunky/python-garminconnect`](https://github.com/cyberjunky/python-garminconnect). It is
separate from the planned first-party Garmin integration in GitHub issue #339. Garmin Connect is
not a supported public API, and Garmin can change or block these login/download flows at any time.
Treat authentication failures as recoverable sync outages.

The current implementation is local-only. It downloads activity FIT files and daily sleep JSON. It
does not upload Garmin data or tokens to the fitness coach server. Server upload is tracked
separately under issue #388.

## Install

From the repository root:

```bash
uv sync --extra garmin
```

The optional `garmin` dependency keeps `garminconnect`, its `curl_cffi` transport, and Typer out of
the deployed API's required dependency set.

Run the upstream project's bundled `example.py` first if you want to prove that your Garmin account
can authenticate independently of this CLI. Login success can vary by account, region, IP, MFA
state, and Garmin rate limits.

## Download a date window

```bash
uv run --extra garmin python -m scripts.garmin_connect download \
  2026-07-01 2026-07-31 \
  --output-dir downloads/garmin-fit
```

The dates are inclusive and must use `YYYY-MM-DD`. The CLI:

1. Reuses tokens from `~/.garminconnect/garmin_tokens.json` when available.
2. Prompts for Garmin email, a hidden password, and MFA when a fresh login is required.
3. Requests Garmin's original activity archive for every activity in the window.
4. Safely reads its sole FIT payload and writes `<garmin-activity-id>.fit` atomically.
5. Skips an existing activity-ID file unless `--overwrite` is supplied.
6. Continues after an individual activity failure, prints a summary, and exits non-zero if any
   activity failed.

Set `GARMIN_EMAIL` or pass `--email` to avoid the email prompt. There is intentionally no password
flag or password environment variable: the password is accepted only through the hidden prompt,
discarded after login, and never written by this project. Renewable tokens are the only Garmin
authentication material persisted; the CLI restricts the token directory to mode `0700` and the
token file to `0600` where the operating system supports POSIX permissions.

## Download sleep data

Garmin exposes sleep through a daily endpoint rather than one range endpoint, so the CLI makes one
request for each date in the inclusive window and preserves each complete response as
`YYYY-MM-DD.json`:

```bash
uv run --extra garmin python -m scripts.garmin_connect sleep \
  2026-07-01 2026-07-07 \
  --output-dir downloads/garmin-sleep
```

Garmin assigns overnight sleep to a calendar date in its response; the filename is the date passed
to Garmin. Existing files are skipped unless `--overwrite` is supplied. Start with a short window
to avoid unnecessary requests or rate limiting. Sleep JSON is sensitive health data and should not
be committed or placed in a shared directory.

Both commands reuse the same authentication tokens. Use a different private token directory if
needed:

```bash
uv run --extra garmin python -m scripts.garmin_connect download \
  2026-07-01 2026-07-31 \
  --token-store ~/.fitness-coach-garmin \
  --output-dir /Volumes/private-activities/garmin
```

Do not put Garmin passwords, tokens, or downloaded FIT files in Git. Run this on a Mac, NAS,
Raspberry Pi, or home server rather than GitHub Actions, Vercel, or shared cloud egress. Avoid
repeated fresh login attempts after a `429` response.

## Local verification

Unit tests mock Garmin and never make a live request:

```bash
uv run pytest tests/python/test_garmin_connect_cli.py
uv run ruff check scripts/garmin_connect.py tests/python/test_garmin_connect_cli.py
uv run ruff format --check scripts/garmin_connect.py tests/python/test_garmin_connect_cli.py
uv run ty check
uv run vulture
```

Then perform the live account test with a small window. The first run should prompt for credentials;
a second run should say the activity files were skipped and should reuse cached tokens without
prompting for the password.
