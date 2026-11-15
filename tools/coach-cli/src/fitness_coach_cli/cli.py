"""Typer command surface. Logic lives in client/credentials/wellness; this only wires I/O."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, NoReturn

import httpx
import typer

from fitness_coach_cli.client import CoachClient
from fitness_coach_cli.credentials import (
    CREDENTIALS_ENV,
    CredentialStore,
    default_credentials_path,
)
from fitness_coach_cli.errors import CoachError
from fitness_coach_cli.tools import ENGINE_OPERATIONS
from fitness_coach_cli.urls import (
    normalize_base_url,
    require_secure_transport,
    requires_confirmation,
)
from fitness_coach_cli.wellness import WellnessMapping, parse_sidecar_json

app = typer.Typer(
    help="Call the fitness-coach HTTP API as the logged-in athlete.",
    no_args_is_help=True,
    add_completion=False,
)
tools_app = typer.Typer(help="Inspect the available API operations.", no_args_is_help=True)
wellness_app = typer.Typer(help="Push recovery data.", no_args_is_help=True)
activities_app = typer.Typer(help="Upload activity files.", no_args_is_help=True)
app.add_typer(tools_app, name="tools")
app.add_typer(wellness_app, name="wellness")
app.add_typer(activities_app, name="activities")

# Tests point the CLI at an httpx.MockTransport by setting this; production leaves it None.
_transport: httpx.BaseTransport | None = None
# Device-flow wait/clock/opener hooks; tests replace them so polling runs instantly.
_sleep = time.sleep
_monotonic = time.monotonic
_open_url = webbrowser.open


@dataclass
class Options:
    base_url: str | None
    credentials_file: Path | None
    json_output: bool


def _options(ctx: typer.Context) -> Options:
    return ctx.obj


def _emit(opts: Options, payload: Any, human: str) -> None:
    if opts.json_output:
        typer.echo(json.dumps(payload, separators=(",", ":")))
    else:
        typer.echo(human)


def _fail(opts: Options, error: CoachError) -> NoReturn:
    if opts.json_output:
        typer.echo(json.dumps(error.as_dict(), separators=(",", ":")), err=True)
    else:
        status = f" (HTTP {error.status})" if error.status else ""
        detail = f": {error.detail}" if error.detail else ""
        typer.echo(f"error: {error.error}{status}{detail}", err=True)
        if error.error == "invite_required":
            typer.echo("hint: re-run `coach login` with --invite-code.", err=True)
    raise typer.Exit(1)


def _base_url(opts: Options) -> str:
    if not opts.base_url:
        raise CoachError("missing_base_url", "Pass --base-url or set COACH_BASE_URL.")
    url = normalize_base_url(opts.base_url)
    require_secure_transport(url)
    return url


def _client(opts: Options) -> CoachClient:
    store = CredentialStore(opts.credentials_file or default_credentials_path())
    return CoachClient(_base_url(opts), store, transport=_transport)


@app.callback()
def root(
    ctx: typer.Context,
    base_url: Annotated[
        str | None,
        typer.Option(
            "--base-url", envvar="COACH_BASE_URL", help="App base URL (required, no default)."
        ),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Compact machine-readable output.")
    ] = False,
    credentials_file: Annotated[
        Path | None,
        typer.Option(
            "--credentials-file",
            envvar=CREDENTIALS_ENV,
            help="Credentials path (default ~/.config/fitness-coach/credentials.json).",
        ),
    ] = None,
) -> None:
    ctx.obj = Options(base_url, credentials_file, json_output)


def _login_step_one(opts: Options, client: CoachClient, email: str, invite: str | None) -> None:
    client.request_otp(email, invite)
    _emit(
        opts,
        {"status": "otp_sent", "email": email},
        f"A login code was emailed to {email}. Then run: coach login --email {email} --code <code>",
    )


def _login_step_two(opts: Options, client: CoachClient, email: str, code: str) -> None:
    creds = client.verify_otp(email, code.strip())
    _emit(
        opts,
        {"status": "logged_in", "user_id": creds.user_id, "base_url": client.base_url},
        f"Logged in to {client.base_url} as user {creds.user_id}.",
    )


def _confirm_target(base_url: str, *, yes: bool) -> None:
    if requires_confirmation(base_url) and not yes:
        raise CoachError(
            "confirmation_required",
            f"{base_url} is not localhost or a *.vercel.app preview; re-run with --yes.",
        )


def _browser_plausible() -> bool:
    """Whether opening a browser can reasonably work: a TTY, and a display on Linux."""
    if not sys.stdout.isatty():
        return False
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


def _try_open_browser(url: str) -> None:
    # A missing or broken browser must never fail login, so any opener error is swallowed.
    with contextlib.suppress(Exception):
        _open_url(url)


def _require_email_for_otp_options(code: str | None, invite_code: str | None) -> None:
    if code is not None or invite_code is not None:
        raise CoachError("invalid_usage", "--code and --invite-code require --email.")


def _login_device(opts: Options, client: CoachClient, *, no_browser: bool) -> None:
    grant = client.start_device_flow()
    if opts.json_output:
        typer.echo(
            json.dumps(
                {
                    "verification_uri": grant["verification_uri"],
                    "verification_uri_complete": grant["verification_uri_complete"],
                    "user_code": grant["user_code"],
                    "expires_in": grant.get("expires_in"),
                },
                separators=(",", ":"),
            )
        )
    else:
        typer.echo(f"Open {grant['verification_uri']} and enter code {grant['user_code']}")
        typer.echo(f"Or open: {grant['verification_uri_complete']}")
        typer.echo("Waiting for approval in the browser (Ctrl-C to cancel)...")
    if not no_browser and _browser_plausible():
        _try_open_browser(grant["verification_uri_complete"])
    creds = client.poll_device_flow(grant, sleep=_sleep, monotonic=_monotonic)
    _emit(
        opts,
        {"status": "logged_in", "user_id": creds.user_id, "base_url": client.base_url},
        f"Logged in to {client.base_url} as user {creds.user_id}.",
    )


@app.command()
def login(
    ctx: typer.Context,
    email: Annotated[
        str | None, typer.Option(help="Use the emailed-code login for this account instead.")
    ] = None,
    code: Annotated[str | None, typer.Option(help="6-digit code from the email (step 2).")] = None,
    invite_code: Annotated[str | None, typer.Option(help="Invite code for new accounts.")] = None,
    no_browser: Annotated[
        bool, typer.Option("--no-browser", help="Device login: do not try to open a browser.")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", help="Confirm a non-local, non-preview (production) target.")
    ] = False,
) -> None:
    """Log in. Default: device flow (approve a code in any browser). --email: emailed code."""
    opts = _options(ctx)
    try:
        _confirm_target(_base_url(opts), yes=yes)
        client = _client(opts)
        if email is None:
            _require_email_for_otp_options(code, invite_code)
            _login_device(opts, client, no_browser=no_browser)
            return
        email = email.strip().lower()
        if code is None:
            _login_step_one(opts, client, email, invite_code)
            if sys.stdin.isatty() and not opts.json_output:
                code = typer.prompt("Code")
        if code is not None:
            _login_step_two(opts, client, email, code)
    except CoachError as exc:
        _fail(opts, exc)


@app.command()
def logout(ctx: typer.Context) -> None:
    """Revoke the session on the server and delete the stored credentials."""
    opts = _options(ctx)
    try:
        revoked = _client(opts).logout()
    except CoachError as exc:
        _fail(opts, exc)
    note = "" if revoked else " (server revocation failed; credentials removed locally)"
    _emit(opts, {"status": "logged_out", "revoked": revoked}, f"Logged out.{note}")


@app.command()
def whoami(ctx: typer.Context) -> None:
    """Show the authenticated user, client and scopes."""
    opts = _options(ctx)
    try:
        info = _client(opts).whoami()
    except CoachError as exc:
        _fail(opts, exc)
    scopes = " ".join(info.get("scopes") or [])
    _emit(
        opts,
        info,
        f"user_id={info.get('user_id')} client_id={info.get('client_id')} scopes={scopes}",
    )


@tools_app.command("list")
def tools_list(ctx: typer.Context) -> None:
    """List known /api/engine operations (usable with `coach call`)."""
    opts = _options(ctx)
    width = max(map(len, ENGINE_OPERATIONS))
    _emit(
        opts,
        [{"operation": name, "description": text} for name, text in ENGINE_OPERATIONS.items()],
        "\n".join(f"{name:<{width}}  {text}" for name, text in ENGINE_OPERATIONS.items()),
    )


def _read_json_body(data: str | None) -> dict[str, Any]:
    if data is None:
        return {}
    try:
        if data == "-":
            raw = sys.stdin.read()
        elif data.startswith("@"):
            raw = Path(data[1:]).read_text(encoding="utf-8")
        else:
            raw = data
        body = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise CoachError("invalid_data", f"--data is not readable JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise CoachError("invalid_data", "--data must be a JSON object.")
    return body


@app.command()
def call(
    ctx: typer.Context,
    operation: Annotated[str, typer.Argument(help="Operation name, e.g. get-athlete-summary.")],
    data: Annotated[
        str | None, typer.Option(help="JSON body: inline, @file, or - for stdin (default {}).")
    ] = None,
) -> None:
    """POST /api/engine/<operation> with a JSON body."""
    opts = _options(ctx)
    try:
        body = _read_json_body(data)
        result = _client(opts).call(operation, body)
    except CoachError as exc:
        _fail(opts, exc)
    _emit(opts, result, json.dumps(result, indent=2))


def _read_text(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    try:
        return Path(source).read_text(encoding="utf-8")
    except OSError as exc:
        raise CoachError("file_unreadable", f"{source}: {exc.strerror or exc}") from exc


def _load_wellness(source: str) -> WellnessMapping:
    mapping = parse_sidecar_json(_read_text(source))
    if mapping.dropped_fields:
        typer.echo(
            "warning: ignored unsupported fields: " + ", ".join(sorted(mapping.dropped_fields)),
            err=True,
        )
    if not mapping.entries:
        raise CoachError("nothing_to_push", "No rows with recovery measurements in input.")
    return mapping


@wellness_app.command("push")
def wellness_push(
    ctx: typer.Context,
    source: Annotated[
        str, typer.Argument(metavar="[FILE|-]", help="Sidecar JSON file, or - for stdin.")
    ] = "-",
) -> None:
    """Save `garmin-sidecar wellness export --format json` rows as recovery data."""
    opts = _options(ctx)
    try:
        mapping = _load_wellness(source)
        result = _client(opts).save_recovery_data(mapping.entries)
    except CoachError as exc:
        _fail(opts, exc)
    count = result.get("count", len(mapping.entries)) if isinstance(result, dict) else 0
    _emit(
        opts,
        {"saved": count, "skipped_dates": mapping.skipped_dates},
        f"Saved {count} recovery entries ({len(mapping.skipped_dates)} empty days skipped).",
    )


def _upload_outcome(client: CoachClient, path: Path) -> dict[str, Any]:
    try:
        result = client.upload_activity(path)
    except CoachError as exc:
        return {"file": str(path), "outcome": "failed", **exc.as_dict()}
    outcome = "course" if result.get("kind") == "course" else "activity"
    return {"file": str(path), "outcome": outcome, "result": result}


def _upload_line(item: dict[str, Any]) -> str:
    if item["outcome"] == "failed":
        return f"failed   {item['file']}: {item['error']}: {item.get('detail')}"
    label = "course" if item["outcome"] == "course" else "saved activity"
    return f"{label:<14} {item['file']}"


@activities_app.command("upload")
def activities_upload(
    ctx: typer.Context,
    files: Annotated[list[Path], typer.Argument(help=".fit, .gpx or .tcx files.")],
) -> None:
    """Upload activity files; a course is analysed, not saved. Exit 1 if any file failed."""
    opts = _options(ctx)
    try:
        client = _client(opts)
    except CoachError as exc:
        _fail(opts, exc)
    outcomes = [_upload_outcome(client, path) for path in files]
    if opts.json_output:
        typer.echo(json.dumps(outcomes, separators=(",", ":")))
    else:
        typer.echo("\n".join(_upload_line(item) for item in outcomes))
    if any(item["outcome"] == "failed" for item in outcomes):
        raise typer.Exit(1)


def main() -> None:
    app()
