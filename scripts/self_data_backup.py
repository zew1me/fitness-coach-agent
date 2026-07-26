"""Back up a local personal-data directory through rclone.

Run with::

    uv run python -m scripts.self_data_backup backup --profile garmin-gdrive
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DEFAULT_CONFIG = Path("~/.config/fitness-coach-agent/backups.toml")
_REMOTE_DESTINATION_RE = re.compile(r"^[^/:\s]+:.+$")


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


def load_config(path: Path) -> BackupConfig:
    """Load and validate an rclone backup configuration."""
    expanded_path = path.expanduser()
    try:
        with expanded_path.open("rb") as config_file:
            raw = tomllib.load(config_file)
    except FileNotFoundError as exc:
        raise BackupConfigError(f"config file does not exist: {expanded_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise BackupConfigError(f"invalid TOML in {expanded_path}: {exc}") from exc

    version = raw.get("version")
    if version != 1:
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
    result = runner(command, check=False, text=True)
    return result.returncode


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Back up local personal data through rclone.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup = subparsers.add_parser(
        "backup", help="Copy a configured local directory to an rclone remote."
    )
    backup.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG,
        help=f"TOML configuration path (default: {_DEFAULT_CONFIG}).",
    )
    backup.add_argument("--profile", required=True, help="Profile name from the configuration.")
    backup.add_argument(
        "--source",
        type=Path,
        help="Override the profile source directory for this run.",
    )
    backup.add_argument(
        "--dry-run", action="store_true", help="Ask rclone to report changes without copying."
    )
    backup.add_argument("--verbose", action="store_true", help="Enable rclone verbose output.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
        return run_backup(
            config=config,
            profile_name=args.profile,
            options=BackupOptions(
                source_override=args.source,
                dry_run=args.dry_run,
                verbose=args.verbose,
            ),
        )
    except BackupConfigError as exc:
        print(f"Backup configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
