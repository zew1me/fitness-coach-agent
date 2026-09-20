"""Local Garmin Connect activity downloader.

Run with::

    uv run --package fitness-coach-garmin-sidecar garmin-sidecar download \
        2026-07-01 2026-07-31 --output-dir ./garmin-fit
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Protocol

import typer
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from .wellness import (
    collect_wellness,
    format_wellness_json,
    format_wellness_text,
    local_timezone_name,
)

app = typer.Typer(
    help="Export local Garmin Connect activities and wellness data.",
    no_args_is_help=True,
)
wellness_app = typer.Typer(help="Export daily Garmin wellness signals.", no_args_is_help=True)
app.add_typer(wellness_app, name="wellness")
_TOKEN_FILENAME = "garmin_tokens.json"
_ACTIVITY_ID_RE = re.compile(r"^[0-9]+$")
_MIN_FIT_HEADER_LENGTH = 12
_FIT_HEADER_SIZES = {12, 14}


class GarminActivityClient(Protocol):
    """Narrow subset of python-garminconnect used by the downloader."""

    def get_activities_by_date(
        self,
        startdate: str,
        enddate: str | None = None,
        activitytype: str | None = None,
        sortorder: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def download_activity(self, activity_id: str, dl_fmt: Any = ...) -> bytes: ...


@dataclass(frozen=True)
class ActivityFailure:
    """One activity that could not be downloaded or materialized."""

    activity_id: str
    message: str


@dataclass
class DownloadSummary:
    """Aggregate result for one date-window download."""

    downloaded: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    failures: list[ActivityFailure] = field(default_factory=list)


def parse_date_window(start: str, end: str) -> tuple[date, date]:
    """Parse and validate an inclusive ISO date window."""
    try:
        start_date = datetime.strptime(start, "%Y-%m-%d").date()
        end_date = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("dates must use YYYY-MM-DD and be valid calendar dates") from exc
    if start_date > end_date:
        raise ValueError("start date must be on or before end date")
    return start_date, end_date


def _is_fit(payload: bytes) -> bool:
    """Recognize the FIT file header without fully parsing the activity."""
    return (
        len(payload) >= _MIN_FIT_HEADER_LENGTH
        and payload[0] in _FIT_HEADER_SIZES
        and payload[8:12] == b".FIT"
    )


def fit_payload_from_original(original: bytes) -> bytes:
    """Return the sole FIT payload from Garmin's original download bytes."""
    if _is_fit(original):
        return original

    archive = io.BytesIO(original)
    if not zipfile.is_zipfile(archive):
        raise ValueError("Garmin returned neither a FIT file nor a valid ZIP archive")

    archive.seek(0)
    try:
        with zipfile.ZipFile(archive) as zipped:
            fit_entries = [
                info
                for info in zipped.infolist()
                if not info.is_dir() and Path(info.filename).suffix.lower() == ".fit"
            ]
            if not fit_entries:
                raise ValueError("Garmin original archive contains no FIT file")
            if len(fit_entries) > 1:
                raise ValueError(
                    f"Garmin original archive contains {len(fit_entries)} FIT files; expected one"
                )
            payload = zipped.read(fit_entries[0])
    except zipfile.BadZipFile as exc:
        raise ValueError("Garmin returned a corrupt ZIP archive") from exc

    if not _is_fit(payload):
        raise ValueError("archive .fit entry does not have a valid FIT header")
    return payload


def _activity_id(activity: dict[str, Any]) -> str:
    raw_id = activity.get("activityId")
    activity_id = str(raw_id) if raw_id is not None else ""
    if not _ACTIVITY_ID_RE.fullmatch(activity_id):
        raise ValueError(f"invalid or missing Garmin activityId: {raw_id!r}")
    return activity_id


def _should_skip_existing(target: Path, *, overwrite: bool) -> bool:
    if overwrite or not target.exists():
        return False
    if not target.is_file():
        raise ValueError(f"destination exists but is not a file: {target}")
    return True


def _mkdir_private(path: Path) -> None:
    """Create path and any missing parent directories, securing each newly created one to 0700."""
    for part in (*reversed(path.parents), path):
        with contextlib.suppress(FileExistsError):
            part.mkdir(mode=0o700)


def _write_private_atomic(path: Path, payload: bytes) -> None:
    """Atomically write a private file in its destination directory."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", dir=path.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.chmod(0o600)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def download_fit_activities(
    client: GarminActivityClient,
    *,
    start: date,
    end: date,
    output_dir: Path,
    overwrite: bool = False,
) -> DownloadSummary:
    """Download all available activities in an inclusive date window."""
    _mkdir_private(output_dir)
    if not output_dir.is_dir():
        raise ValueError(f"output path is not a directory: {output_dir}")

    activities = client.get_activities_by_date(start.isoformat(), end.isoformat(), sortorder="asc")
    summary = DownloadSummary()

    for activity in activities:
        try:
            activity_id = _activity_id(activity)
            target = output_dir / f"{activity_id}.fit"
            if _should_skip_existing(target, overwrite=overwrite):
                summary.skipped.append(target)
                continue

            original = client.download_activity(
                activity_id, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL
            )
            payload = fit_payload_from_original(original)
            _write_private_atomic(target, payload)
            summary.downloaded.append(target)
        except Exception as exc:
            raw_id = activity.get("activityId")
            summary.failures.append(
                ActivityFailure(
                    activity_id=str(raw_id) if raw_id is not None else "unknown",
                    message=str(exc),
                )
            )

    return summary


def token_file_path(token_store: Path) -> Path:
    """Return the token file written by python-garminconnect."""
    return token_store.expanduser() / _TOKEN_FILENAME


def _secure_token_store(token_store: Path) -> None:
    token_file = token_file_path(token_store)
    if token_store.is_dir():
        token_store.chmod(0o700)
    if token_file.is_file():
        token_file.chmod(0o600)


def authenticate(*, token_store: Path, email: str | None) -> Garmin:
    """Reuse cached tokens or perform one interactive credential login."""
    token_store = token_store.expanduser()
    token_file = token_file_path(token_store)

    if token_file.is_file():
        _secure_token_store(token_store)
        cached_client = Garmin()
        try:
            cached_client.login(str(token_store))
        except GarminConnectAuthenticationError:
            typer.secho("Saved Garmin tokens are no longer valid; login is required.", fg="yellow")
        except (GarminConnectConnectionError, GarminConnectTooManyRequestsError):
            # Do not turn an outage or rate limit into a credential-login storm.
            raise
        else:
            return cached_client

    resolved_email = email or typer.prompt("Garmin email").strip()
    password = typer.prompt("Garmin password", hide_input=True)
    client = Garmin(
        email=resolved_email,
        password=password,
        prompt_mfa=lambda: typer.prompt("Garmin MFA code").strip(),
    )
    _mkdir_private(token_store)
    try:
        client.login(str(token_store))
    finally:
        client.password = None
        password = ""

    _secure_token_store(token_store)
    return client


def _setup_error_message(exc: Exception) -> str:
    if isinstance(exc, GarminConnectTooManyRequestsError):
        return f"Garmin rate-limited the request; keep cached tokens and retry later: {exc}"
    if isinstance(exc, GarminConnectAuthenticationError):
        return f"Garmin authentication failed: {exc}"
    if isinstance(exc, GarminConnectConnectionError):
        return f"Garmin connection failed: {exc}"
    return f"Garmin setup failed: {exc}"


@app.callback()
def cli() -> None:
    """Run local Garmin Connect sidecar commands."""


@app.command()
def download(
    start: Annotated[str, typer.Argument(help="Inclusive start date (YYYY-MM-DD).")],
    end: Annotated[str, typer.Argument(help="Inclusive end date (YYYY-MM-DD).")],
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir",
            "-o",
            help="Directory for activity-ID.fit files.",
        ),
    ] = Path("downloads/garmin-fit"),
    token_store: Annotated[
        Path,
        typer.Option(
            "--token-store",
            help="Private python-garminconnect token directory.",
        ),
    ] = Path("~/.garminconnect"),
    email: Annotated[
        str | None,
        typer.Option(
            "--email",
            envvar="GARMIN_EMAIL",
            help="Garmin email. Passwords are always entered through a hidden prompt.",
        ),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite",
            help="Replace existing activity-ID.fit files.",
        ),
    ] = False,
) -> None:
    """Download original FIT activities in an inclusive date window."""
    try:
        start_date, end_date = parse_date_window(start, end)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    try:
        client = authenticate(token_store=token_store, email=email)
        summary = download_fit_activities(
            client,
            start=start_date,
            end=end_date,
            output_dir=output_dir.expanduser(),
            overwrite=overwrite,
        )
    except (
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
        OSError,
        ValueError,
    ) as exc:
        message = _setup_error_message(exc)
        typer.secho(message, fg="red", err=True)
        raise typer.Exit(code=2) from exc

    for path in summary.downloaded:
        typer.echo(f"downloaded {path}")
    for failure in summary.failures:
        typer.secho(f"failed {failure.activity_id}: {failure.message}", fg="red", err=True)

    typer.echo(
        f"Summary: {len(summary.downloaded)} downloaded, "
        f"{len(summary.skipped)} skipped, {len(summary.failures)} failed."
    )
    if summary.failures:
        raise typer.Exit(code=1)


@wellness_app.command("export")
def wellness_export(
    start: Annotated[str, typer.Argument(help="Inclusive start date (YYYY-MM-DD).")],
    end: Annotated[str, typer.Argument(help="Inclusive end date (YYYY-MM-DD).")],
    output_format: Annotated[
        str,
        typer.Option(
            "--format",
            help="Output format: text for chat paste or json for automation.",
        ),
    ] = "text",
    token_store: Annotated[
        Path,
        typer.Option(
            "--token-store",
            help="Private python-garminconnect token directory.",
        ),
    ] = Path("~/.garminconnect"),
    email: Annotated[
        str | None,
        typer.Option(
            "--email",
            envvar="GARMIN_EMAIL",
            help="Garmin email. Passwords are always entered through a hidden prompt.",
        ),
    ] = None,
) -> None:
    """Export recovery fields for an inclusive date window."""
    try:
        start_date, end_date = parse_date_window(start, end)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if output_format not in {"text", "json"}:
        raise typer.BadParameter("format must be 'text' or 'json'", param_hint="--format")

    try:
        client = authenticate(token_store=token_store, email=email)
        summary = collect_wellness(
            client,
            start=start_date,
            end=end_date,
        )
    except (
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
    ) as exc:
        typer.secho(_setup_error_message(exc), fg="red", err=True)
        raise typer.Exit(code=2) from exc

    if output_format == "json":
        typer.echo(format_wellness_json(summary.rows))
    else:
        typer.echo(
            format_wellness_text(
                summary.rows,
                start=start_date,
                end=end_date,
                timezone_name=local_timezone_name(),
            )
        )

    for failure in summary.failures:
        typer.secho(
            f"failed {failure.log_date.isoformat()} {failure.metric}: {failure.message}",
            fg="red",
            err=True,
        )
    if summary.failures:
        failed_days = len({failure.log_date for failure in summary.failures})
        typer.secho(
            f"Summary: {len(summary.rows)} days exported, {len(summary.failures)} "
            f"metric requests failed across {failed_days} days.",
            fg="red",
            err=True,
        )
        raise typer.Exit(code=1)


def main() -> None:
    """Run the Typer application."""
    app()


if __name__ == "__main__":
    main()
