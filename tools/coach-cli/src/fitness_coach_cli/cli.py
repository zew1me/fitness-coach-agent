"""Typer commands for a logged-in athlete; every operation uses deployment HTTP APIs."""

from __future__ import annotations

import json
import re
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Never
from urllib.parse import urlsplit

import httpx
import typer

from fitness_coach_cli.approval import browser_login
from fitness_coach_cli.client import CoachClient, stream_events
from fitness_coach_cli.credentials import CoachError, CredentialStore, default_store_path

app = typer.Typer(
    no_args_is_help=True, help="Act as a logged-in athlete against an explicit deployment."
)
tools_app = typer.Typer(no_args_is_help=True)
wellness_app = typer.Typer(no_args_is_help=True)
activities_app = typer.Typer(no_args_is_help=True)
chat_app = typer.Typer(no_args_is_help=True)
app.add_typer(tools_app, name="tools")
app.add_typer(wellness_app, name="wellness")
app.add_typer(activities_app, name="activities")
app.add_typer(chat_app, name="chat")

_OPERATIONS = {
    "get-athlete-summary": "Read athlete profile and fitness context.",
    "get-recent-activities": "Read recent recorded activities.",
    "get-compliance-summary": "Read training-plan compliance.",
    "calculate-zones": "Calculate training zones.",
    "estimate-thresholds": "Estimate sport thresholds.",
    "recalibrate-thresholds": "Propose threshold recalibration.",
    "update-athlete-profile": "Update the athlete profile.",
    "update-goals": "Update training goals.",
    "update-schedule": "Update availability and dated overrides.",
    "generate-plan-structure": "Generate a new plan (supersedes the active plan).",
    "adjust-plan": "Recompose future workouts on the active plan.",
    "find-plan-workout": "Find a planned workout.",
    "resolve-plan-workout": "Resolve workout completion.",
    "save-recovery-data": "Save daily recovery and wellness entries.",
    "save-activity-from-text": "Record a described completed activity.",
}
_CONTENT_TYPES = {
    ".fit": "application/vnd.garmin.fit",
    ".gpx": "application/gpx+xml",
    ".tcx": "application/vnd.garmin.tcx+xml",
    ".zip": "application/zip",
}
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@dataclass
class Options:
    base_url: str | None
    credentials: Path
    json_output: bool


def _options(ctx: typer.Context) -> Options:
    return ctx.obj


def _error(error: CoachError) -> Never:
    typer.echo(
        json.dumps({"error": "coach_error", "status": error.status, "detail": str(error)}), err=True
    )
    raise typer.Exit(1)


@contextmanager
def _client(ctx: typer.Context):
    options = _options(ctx)
    if not options.base_url:
        _error(CoachError("Provide --base-url (or COACH_BASE_URL); no deployment is implicit."))
    try:
        with CoachClient(options.base_url, CredentialStore(options.credentials)) as client:
            yield client
    except CoachError as error:
        _error(error)
    except httpx.HTTPError:
        _error(
            CoachError(
                "Network request failed; check the target. Writes are not automatically replayed."
            )
        )
    except (ValueError, UnicodeError):
        _error(CoachError("Invalid JSON or text returned by the deployment or supplied as input."))
    except OSError:
        _error(
            CoachError(
                "Cannot access local files or bind the callback port; check permissions and paths."
            )
        )


def _output(ctx: typer.Context, payload: Any) -> None:
    typer.echo(
        json.dumps(payload, indent=None if _options(ctx).json_output else 2, ensure_ascii=False)
    )


def _read_json(value: str) -> Any:
    if value == "-":
        return json.load(sys.stdin)
    if value.startswith("@"):
        return json.loads(Path(value[1:]).expanduser().read_text(encoding="utf-8"))
    return json.loads(value)


@app.callback()
def configure(
    ctx: typer.Context,
    base_url: Annotated[str | None, typer.Option(envvar="COACH_BASE_URL")] = None,
    credentials: Annotated[
        Path | None, typer.Option(help="Private credential store; never a Supabase key.")
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Compact JSON; JSONL for chat events.")
    ] = False,
) -> None:
    ctx.obj = Options(base_url, credentials or default_store_path(), json_output)


@app.command()
def login(
    ctx: typer.Context,
    callback_url: Annotated[
        str, typer.Option(help="Loopback callback; port 0 selects a free port.")
    ] = "http://127.0.0.1:0/callback",
    open_browser: Annotated[
        bool, typer.Option("--browser/--no-browser", help="Open the approval URL automatically.")
    ] = True,
    timeout: Annotated[int, typer.Option(min=1, max=1800)] = 300,
    yes: Annotated[
        bool, typer.Option("--yes", help="Explicitly confirm this remote deployment.")
    ] = False,
) -> None:
    """Approve access in the browser (email OTP/login + consent); store only OAuth tokens."""
    with _client(ctx) as client:
        if urlsplit(client.base_url).hostname not in {"localhost", "127.0.0.1", "::1"} and not yes:
            typer.confirm(f"Log in to remote deployment {client.base_url}?", abort=True, err=True)
        browser_login(
            client,
            callback_url=callback_url,
            open_browser=open_browser,
            timeout=timeout,
            announce=lambda text: typer.echo(text, err=True),
        )
        _output(ctx, {"logged_in": True, "base_url": client.base_url})


@app.command()
def whoami(ctx: typer.Context) -> None:
    """Validate the current grant and show the logged-in athlete."""
    with _client(ctx) as client:
        _output(ctx, client.request("GET", "/api/auth/whoami"))


@app.command()
def logout(ctx: typer.Context) -> None:
    """Revoke the current OAuth grant remotely, then remove this target's local tokens."""
    with _client(ctx) as client:
        removed = client.logout()
        _output(
            ctx,
            {
                "grant_revoked": True,
                "logged_out": removed,
                "newer_login_preserved": not removed,
                "base_url": client.base_url,
            },
        )


@app.command("call")
def call_operation(
    ctx: typer.Context,
    operation: str,
    data: Annotated[str, typer.Option(help="JSON, @file, or - for stdin.")] = "{}",
) -> None:
    """POST to /api/engine/OPERATION using the deployment's existing validation and logic."""
    with _client(ctx) as client:
        if re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", operation) is None:
            raise CoachError("Operation must be a hyphenated name, not a URL or path.")
        payload = _read_json(data)
        if not isinstance(payload, dict):
            raise CoachError("--data must contain a JSON object.")
        _output(ctx, client.request("POST", f"/api/engine/{operation}", json=payload))


@tools_app.command("list")
def tools_list(ctx: typer.Context) -> None:
    """List known engine operations (call also accepts other deployment operation names)."""
    _output(ctx, [{"operation": key, "description": value} for key, value in _OPERATIONS.items()])


@wellness_app.command("push")
def wellness_push(ctx: typer.Context, file: Annotated[str, typer.Argument()] = "-") -> None:
    """Push Garmin sidecar JSON (a list of entries) without changing its provenance."""
    with _client(ctx) as client:
        payload = _read_json("-" if file == "-" else f"@{file}")
        entries = payload.get("entries") if isinstance(payload, dict) else payload
        if (
            not isinstance(entries, list)
            or not entries
            or not all(isinstance(e, dict) for e in entries)
        ):
            raise CoachError("Expected a nonempty JSON entry list or an object containing entries.")
        _output(
            ctx, client.request("POST", "/api/engine/save-recovery-data", json={"entries": entries})
        )


def _upload(client: CoachClient, path: Path) -> Any:
    content_type = _CONTENT_TYPES.get(path.suffix.lower())
    if content_type is None:
        raise CoachError("Activity uploads support .fit, .gpx, .tcx, and .zip files.")
    with path.open("rb") as source:
        content = source.read(_MAX_UPLOAD_BYTES + 1)
    if not content or len(content) > _MAX_UPLOAD_BYTES:
        raise CoachError("Activity files must be nonempty and no larger than 25 MiB.")
    signed = client.request(
        "POST",
        "/api/files/presign-upload",
        json={
            "filename": path.name,
            "content_type": content_type,
            "content_length": len(content),
            "purpose": "activity-upload",
        },
    )
    object_key = signed.get("object_key") if isinstance(signed, dict) else None
    if not isinstance(object_key, str) or not object_key:
        raise CoachError("Deployment returned an invalid upload reference.")
    uploaded = client.request(
        "POST",
        "/api/chat/attachments/upload",
        data={"object_key": object_key},
        files={"file": (path.name, content, content_type)},
    )
    if not isinstance(uploaded, dict) or not isinstance(uploaded.get("public_url"), str):
        raise CoachError("Deployment returned an invalid uploaded-file reference.")
    # The proxy keeps all CLI traffic on the deployment; no direct R2 or presigned-URL fetches.
    operation = "process-uploaded-zip" if path.suffix.lower() == ".zip" else "process-uploaded-file"
    return client.request(
        "POST",
        f"/api/engine/{operation}",
        json={
            "filename": path.name,
            "content_type": content_type,
            "object_key": object_key,
            "public_url": uploaded.get("public_url"),
        },
    )


@activities_app.command("upload")
def activities_upload(ctx: typer.Context, files: Annotated[list[Path], typer.Argument()]) -> None:
    """Use the existing presign → upload proxy → activity/course processing pipeline."""
    with _client(ctx) as client:
        for file in files:
            _output(ctx, {"filename": file.name, "result": _upload(client, file.expanduser())})


@chat_app.command("send")
def chat_send(
    ctx: typer.Context,
    message: str,
    stream: Annotated[bool, typer.Option(help="Print text deltas immediately.")] = False,
) -> None:
    """Send a turn in the same athlete thread used by the web coach."""
    with _client(ctx) as client:
        payload = {
            "message": {
                "id": str(uuid.uuid4()),
                "role": "user",
                "parts": [
                    {"type": "text", "text": message},
                ],
            }
        }
        with client.stream(payload) as response:
            _render_events(ctx, response, stream=stream)


def _render_events(ctx: typer.Context, response: httpx.Response, *, stream: bool) -> None:
    text: list[str] = []
    for event in stream_events(response):
        if _options(ctx).json_output:
            _output(ctx, event)
        elif event.get("type") == "text-delta":
            delta = event.get("delta")
            if not isinstance(delta, str):
                raise CoachError("Invalid chat text delta.")
            if stream:
                typer.echo(delta, nl=False)
                sys.stdout.flush()
            else:
                text.append(delta)
    if not _options(ctx).json_output:
        typer.echo("" if stream else "".join(text))


@chat_app.command("history")
def chat_history(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
) -> None:
    """Read recent persisted athlete-thread messages."""
    with _client(ctx) as client:
        _output(ctx, client.request("GET", "/api/chat/messages", params={"limit": limit}))


def main() -> None:
    """Run the installed coach entry point."""
    app()
