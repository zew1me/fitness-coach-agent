"""``coach``: act as a signed-in athlete against a fitness-coach deployment.

Run with::

    uv run --package fitness-coach-cli coach --base-url https://<deployment> login --email me@x.com
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, NoReturn

import httpx
import typer

from fitness_coach_cli.client import CoachClient
from fitness_coach_cli.credentials import CredentialStore, default_credentials_path
from fitness_coach_cli.errors import CoachError
from fitness_coach_cli.operations import OPERATIONS
from fitness_coach_cli.target import is_local, normalize_base_url

app = typer.Typer(
    help="Headless fitness-coach client: sign in as an athlete and call coach operations.",
    no_args_is_help=True,
)
tools_app = typer.Typer(
    help="Inspect the coach operations this CLI can call.", no_args_is_help=True
)
app.add_typer(tools_app, name="tools")

_NON_PRODUCTION_ENVIRONMENTS = frozenset({"development", "preview"})


@dataclass
class Options:
    base_url: str | None
    credentials_file: Path
    json_output: bool
    timeout: float
    # Tests pass an in-process transport through ``CliRunner.invoke(obj={"transport": ...})``.
    transport: httpx.BaseTransport | None = None

    def client(self) -> CoachClient:
        if self.base_url is None:
            raise CoachError(
                "missing_base_url",
                "No deployment selected.",
                hint="Pass --base-url https://<deployment> or set COACH_BASE_URL.",
            )
        return CoachClient(
            normalize_base_url(self.base_url),
            CredentialStore(self.credentials_file),
            timeout=self.timeout,
            bypass_secret=os.environ.get("VERCEL_AUTOMATION_BYPASS_SECRET"),
            transport=self.transport,
        )


def _interactive() -> bool:
    return sys.stdin.isatty()


def _options(ctx: typer.Context) -> Options:
    options = ctx.find_root().obj
    if not isinstance(options, Options):
        raise TypeError("coach options were not initialised")
    return options


def _emit(options: Options, payload: Any, text: str | None = None) -> None:
    if options.json_output:
        typer.echo(json.dumps(payload, separators=(",", ":"), default=str))
    elif text is not None:
        typer.echo(text)
    else:
        typer.echo(json.dumps(payload, indent=2, default=str))


def _fail(options: Options, error: CoachError) -> NoReturn:
    if options.json_output:
        typer.echo(json.dumps(error.to_json(), separators=(",", ":")), err=True)
    else:
        status = f" (HTTP {error.status})" if error.status is not None else ""
        typer.secho(f"Error{status}: {error.detail}", fg="red", err=True)
        if error.hint:
            typer.echo(error.hint, err=True)
    raise typer.Exit(code=1)


@app.callback()
def root(
    ctx: typer.Context,
    base_url: Annotated[
        str | None,
        typer.Option(
            "--base-url",
            envvar="COACH_BASE_URL",
            help="Deployment origin, e.g. https://coach.example.com or http://localhost:3000.",
        ),
    ] = None,
    credentials_file: Annotated[
        Path | None,
        typer.Option(
            "--credentials-file",
            envvar="COACH_CREDENTIALS_FILE",
            help="Private token store (default: ~/.config/fitness-coach/credentials.json).",
        ),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Compact JSON output; errors as JSON on stderr.")
    ] = False,
    timeout: Annotated[float, typer.Option("--timeout", min=1.0, help="Seconds per request.")] = (
        120.0
    ),
) -> None:
    """Every deployment is explicit: pass --base-url (or COACH_BASE_URL) on each run."""
    seams = ctx.obj if isinstance(ctx.obj, dict) else {}
    ctx.obj = Options(
        base_url=base_url,
        credentials_file=credentials_file or default_credentials_path(),
        json_output=json_output,
        timeout=timeout,
        transport=seams.get("transport"),
    )


def _confirm_target(options: Options, client: CoachClient, *, assume_yes: bool) -> None:
    """Require explicit confirmation before signing in to production (or an unknown target)."""
    if assume_yes or is_local(client.base_url):
        return
    environment = client.environment()
    if environment in _NON_PRODUCTION_ENVIRONMENTS:
        return
    label = environment or "an unidentified deployment"
    if not _interactive():
        _fail(
            options,
            CoachError(
                "confirmation_required",
                f"{client.base_url} is {label}; signing in needs confirmation.",
                hint="Re-run with --yes to confirm.",
            ),
        )
    if not typer.confirm(f"{client.base_url} is {label}. Sign in as a real athlete?"):
        raise typer.Exit(code=1)


@app.command()
def login(
    ctx: typer.Context,
    email: Annotated[str, typer.Option("--email", help="Athlete email address.")],
    code: Annotated[
        str | None,
        typer.Option("--code", help="The code from the sign-in email (second step)."),
    ] = None,
    invite_code: Annotated[
        str | None,
        typer.Option("--invite-code", help="Invite code, required for a first-time email."),
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Confirm signing in to production.")
    ] = False,
) -> None:
    """Sign in with an emailed code: run once to send it, again with --code to finish.

    In an interactive terminal the first step prompts for the code instead of exiting.
    """
    options = _options(ctx)
    email = email.strip().lower()
    try:
        with options.client() as client:
            _confirm_target(options, client, assume_yes=yes)
            if code is None:
                client.request_code(email, invite_code)
                if not _interactive() or options.json_output:
                    _emit(
                        options,
                        {"status": "code_sent", "email": email, "base_url": client.base_url},
                        f"Sign-in code sent to {email}. Finish with:\n"
                        f"  coach --base-url {client.base_url} login --email {email} "
                        "--code <code>",
                    )
                    return
                code = typer.prompt("Code from the email").strip()
            credentials = client.verify_code(email, code.strip())
            _emit(
                options,
                {
                    "status": "logged_in",
                    "user_id": credentials.user_id,
                    "base_url": client.base_url,
                },
                f"Signed in to {client.base_url} as {credentials.user_id}.",
            )
    except CoachError as exc:
        _fail(options, exc)


@app.command()
def logout(ctx: typer.Context) -> None:
    """Revoke this machine's sign-in server-side and delete the local tokens."""
    options = _options(ctx)
    messages = {
        "revoked": "Signed out; the grant was revoked.",
        "already_invalid": "Signed out; the sign-in had already expired or been revoked.",
        "not_logged_in": "Not signed in; nothing to do.",
    }
    try:
        with options.client() as client:
            outcome = client.logout()
            _emit(options, {"status": outcome, "base_url": client.base_url}, messages[outcome])
    except CoachError as exc:
        _fail(options, exc)


@app.command()
def whoami(ctx: typer.Context) -> None:
    """Show the athlete and scopes the stored sign-in resolves to on the server."""
    options = _options(ctx)
    try:
        with options.client() as client:
            identity = client.whoami()
            identity["base_url"] = client.base_url
            scopes = " ".join(identity.get("scopes") or [])
            _emit(
                options,
                identity,
                f"{identity.get('user_id')} on {client.base_url}\n"
                f"client: {identity.get('client_id')}\nscopes: {scopes}",
            )
    except CoachError as exc:
        _fail(options, exc)


def _read_payload(data: str | None) -> dict[str, Any]:
    if data is None:
        return {}
    if data == "-":
        raw, source = sys.stdin.read(), "stdin"
    elif data.startswith("@"):
        path = Path(data[1:]).expanduser()
        try:
            raw, source = path.read_text(encoding="utf-8"), str(path)
        except OSError as exc:
            raise CoachError("invalid_input", f"Cannot read {path}: {exc.strerror}") from exc
    else:
        raw, source = data, "--data"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CoachError("invalid_input", f"{source} is not valid JSON: {exc.msg}.") from exc
    if not isinstance(payload, dict):
        raise CoachError("invalid_input", f"{source} must be a JSON object.")
    return payload


@app.command()
def call(
    ctx: typer.Context,
    operation: Annotated[str, typer.Argument(help="Operation name; see `coach tools list`.")],
    data: Annotated[
        str | None,
        typer.Option("--data", "-d", help="JSON object body: inline JSON, @file, or - for stdin."),
    ] = None,
) -> None:
    """Call one coach operation (POST /api/engine/<operation>) and print its JSON result."""
    options = _options(ctx)
    try:
        payload = _read_payload(data)
        with options.client() as client:
            _emit(options, client.call(operation, payload))
    except CoachError as exc:
        _fail(options, exc)


@tools_app.command("list")
def tools_list(ctx: typer.Context) -> None:
    """List the operations `coach call` can reach, with one-line descriptions."""
    options = _options(ctx)
    entries = [{"name": name, "description": text} for name, text in sorted(OPERATIONS.items())]
    width = max(len(name) for name in OPERATIONS)
    text = "\n".join(f"{entry['name']:<{width}}  {entry['description']}" for entry in entries)
    _emit(options, entries, text)


def main() -> None:
    app()
