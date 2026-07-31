"""Back up configured local personal-data directories through rclone."""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer

_DEFAULT_CONFIG = Path("~/.config/fitness-coach-agent/backups.toml")
_REMOTE_DESTINATION_RE = re.compile(r"^(?!-)[^/:\s]+:.+$")

app = typer.Typer(
    help="Back up local personal data through rclone.",
    no_args_is_help=True,
)


class BackupConfigError(ValueError):
    """Raised when the local backup configuration is invalid."""


@dataclass(frozen=True)
class RcloneSettings:
    binary: str = "rclone"
    config: Path | None = None


@dataclass(frozen=True)
class BackupProfile:
    name: str
    source: Path
    destination: str
    checksum: bool = True


@dataclass(frozen=True)
class BackupConfig:
    rclone: RcloneSettings
    profiles: dict[str, BackupProfile]


@dataclass(frozen=True)
class BackupOptions:
    source_override: Path | None = None
    dry_run: bool = False
    verbose: bool = False


_DEFAULT_BACKUP_OPTIONS = BackupOptions()
RunCommand = Callable[..., subprocess.CompletedProcess[str]]


def _table(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BackupConfigError(f"{name} must be a TOML table")
    return value


def _non_empty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BackupConfigError(f"{name} must be a non-empty string")
    return value


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as config_file:
            return tomllib.load(config_file)
    except FileNotFoundError as exc:
        raise BackupConfigError(f"config file does not exist: {path}") from exc
    except OSError as exc:
        raise BackupConfigError(f"cannot read config file {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise BackupConfigError(f"invalid TOML in {path}: {exc}") from exc


def load_config(path: Path) -> BackupConfig:
    """Load and validate an rclone backup configuration."""
    raw = _read_toml(path.expanduser())

    if raw.get("version") != 1:
        raise BackupConfigError("config version must be 1")

    raw_rclone = _table(raw.get("rclone", {}), "rclone")
    binary = _non_empty_string(raw_rclone.get("binary", "rclone"), "rclone.binary")
    raw_rclone_config = raw_rclone.get("config")
    rclone_config = (
        Path(_non_empty_string(raw_rclone_config, "rclone.config")).expanduser()
        if raw_rclone_config is not None
        else None
    )

    raw_profiles = _table(raw.get("profiles"), "profiles")
    if not raw_profiles:
        raise BackupConfigError("profiles must contain at least one backup profile")

    profiles: dict[str, BackupProfile] = {}
    for name, raw_profile_value in raw_profiles.items():
        raw_profile = _table(raw_profile_value, f"profiles.{name}")
        source = Path(
            _non_empty_string(raw_profile.get("source"), f"profiles.{name}.source")
        ).expanduser()
        destination = _non_empty_string(
            raw_profile.get("destination"), f"profiles.{name}.destination"
        )
        if not _REMOTE_DESTINATION_RE.fullmatch(destination):
            raise BackupConfigError(
                f"profiles.{name}.destination must be an rclone remote path such as "
                "gdrive:fitness-coach/garmin-fit"
            )
        checksum = raw_profile.get("checksum", True)
        if not isinstance(checksum, bool):
            raise BackupConfigError(f"profiles.{name}.checksum must be a boolean")
        profiles[name] = BackupProfile(
            name=name,
            source=source,
            destination=destination,
            checksum=checksum,
        )

    return BackupConfig(
        rclone=RcloneSettings(binary=binary, config=rclone_config),
        profiles=profiles,
    )


def build_rclone_command(
    *,
    settings: RcloneSettings,
    profile: BackupProfile,
    source: Path,
    dry_run: bool,
    verbose: bool,
) -> list[str]:
    """Build the non-destructive rclone copy command for a profile."""
    command = [settings.binary, "copy", str(source), profile.destination]
    if settings.config is not None:
        command.extend(["--config", str(settings.config)])
    if profile.checksum:
        command.append("--checksum")
    if dry_run:
        command.append("--dry-run")
    if verbose:
        command.append("--verbose")
    return command


def run_backup(
    *,
    config: BackupConfig,
    profile_name: str,
    options: BackupOptions = _DEFAULT_BACKUP_OPTIONS,
    runner: RunCommand = subprocess.run,
) -> int:
    """Validate a profile and copy its source directory without remote deletion."""
    try:
        profile = config.profiles[profile_name]
    except KeyError as exc:
        available = ", ".join(sorted(config.profiles))
        raise BackupConfigError(
            f"unknown backup profile {profile_name!r}; available profiles: {available}"
        ) from exc

    source = (options.source_override or profile.source).expanduser().resolve()
    if not source.is_dir():
        raise BackupConfigError(f"backup source is not a directory: {source}")
    if config.rclone.config is not None and not config.rclone.config.is_file():
        raise BackupConfigError(f"rclone config file does not exist: {config.rclone.config}")
    if shutil.which(config.rclone.binary) is None:
        raise BackupConfigError(
            f"rclone executable not found: {config.rclone.binary!r}; install rclone first"
        )

    command = build_rclone_command(
        settings=config.rclone,
        profile=profile,
        source=source,
        dry_run=options.dry_run,
        verbose=options.verbose,
    )
    try:
        result = runner(command, check=False, text=True)
    except OSError as exc:
        raise BackupConfigError(f"unable to execute rclone: {exc}") from exc
    return result.returncode


@app.callback()
def cli() -> None:
    """Manage local personal-data backups."""


@app.command()
def backup(
    profile: Annotated[
        str,
        typer.Option("--profile", help="Profile name from the configuration."),
    ],
    config_path: Annotated[
        Path,
        typer.Option(
            "--config",
            help=f"TOML configuration path (default: {_DEFAULT_CONFIG}).",
        ),
    ] = _DEFAULT_CONFIG,
    source: Annotated[
        Path | None,
        typer.Option("--source", help="Override the profile source directory for this run."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report changes without copying."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Enable rclone verbose output."),
    ] = False,
) -> None:
    """Copy a configured local directory to an rclone remote."""
    try:
        config = load_config(config_path)
        exit_code = run_backup(
            config=config,
            profile_name=profile,
            options=BackupOptions(
                source_override=source,
                dry_run=dry_run,
                verbose=verbose,
            ),
        )
    except BackupConfigError as exc:
        typer.echo(f"Backup configuration error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if exit_code:
        raise typer.Exit(code=exit_code)


def main() -> None:
    """Run the Typer application."""
    app()
