from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.self_data_backup import (
    BackupConfig,
    BackupConfigError,
    BackupOptions,
    BackupProfile,
    RcloneSettings,
    build_rclone_command,
    load_config,
    main,
    run_backup,
)


def _write_config(path: Path, source: Path, *, destination: str = "gdrive:backups/garmin") -> None:
    path.write_text(
        f'''version = 1

[rclone]
binary = "rclone"

[profiles.garmin]
source = "{source}"
destination = "{destination}"
checksum = true
''',
        encoding="utf-8",
    )


def test_load_config_reads_rclone_and_profile(tmp_path: Path) -> None:
    source = tmp_path / "garmin-fit"
    config_path = tmp_path / "backups.toml"
    _write_config(config_path, source)

    config = load_config(config_path)

    assert config.rclone == RcloneSettings(binary="rclone")
    assert config.profiles["garmin"] == BackupProfile(
        name="garmin",
        source=source,
        destination="gdrive:backups/garmin",
        checksum=True,
    )


@pytest.mark.parametrize(
    ("config_text", "message"),
    [
        ("version = 2\n[profiles]\n", "config version must be 1"),
        (
            'version = 1\n[profiles.garmin]\nsource = "data"\ndestination = "/tmp/backup"\n',
            "must be an rclone remote path",
        ),
        (
            'version = 1\n[profiles.garmin]\nsource = "data"\n'
            'destination = "gdrive:data"\nchecksum = "yes"\n',
            "checksum must be a boolean",
        ),
    ],
)
def test_load_config_rejects_invalid_values(tmp_path: Path, config_text: str, message: str) -> None:
    config_path = tmp_path / "backups.toml"
    config_path.write_text(config_text, encoding="utf-8")

    with pytest.raises(BackupConfigError, match=message):
        load_config(config_path)


def test_build_rclone_command_uses_copy_and_requested_flags(tmp_path: Path) -> None:
    command = build_rclone_command(
        settings=RcloneSettings(binary="/opt/homebrew/bin/rclone", config=tmp_path / "rclone.conf"),
        profile=BackupProfile(
            name="garmin",
            source=tmp_path / "unused",
            destination="gdrive:fitness/garmin",
        ),
        source=tmp_path / "actual",
        dry_run=True,
        verbose=True,
    )

    assert command == [
        "/opt/homebrew/bin/rclone",
        "copy",
        str(tmp_path / "actual"),
        "gdrive:fitness/garmin",
        "--config",
        str(tmp_path / "rclone.conf"),
        "--checksum",
        "--dry-run",
        "--verbose",
    ]
    assert "sync" not in command


def test_run_backup_invokes_rclone_with_source_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured_source = tmp_path / "configured"
    override_source = tmp_path / "override"
    override_source.mkdir()
    config = BackupConfig(
        rclone=RcloneSettings(),
        profiles={
            "garmin": BackupProfile(
                name="garmin",
                source=configured_source,
                destination="gdrive:fitness/garmin",
            )
        },
    )
    calls: list[list[str]] = []

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("scripts.self_data_backup.shutil.which", lambda _: "/usr/bin/rclone")

    exit_code = run_backup(
        config=config,
        profile_name="garmin",
        options=BackupOptions(source_override=override_source, dry_run=True),
        runner=runner,
    )

    assert exit_code == 0
    assert calls == [
        [
            "rclone",
            "copy",
            str(override_source.resolve()),
            "gdrive:fitness/garmin",
            "--checksum",
            "--dry-run",
        ]
    ]


def test_run_backup_rejects_missing_source(tmp_path: Path) -> None:
    config = BackupConfig(
        rclone=RcloneSettings(),
        profiles={
            "garmin": BackupProfile(
                name="garmin",
                source=tmp_path / "missing",
                destination="gdrive:fitness/garmin",
            )
        },
    )

    with pytest.raises(BackupConfigError, match="backup source is not a directory"):
        run_backup(config=config, profile_name="garmin")


def test_main_reports_unknown_profile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "garmin-fit"
    source.mkdir()
    config_path = tmp_path / "backups.toml"
    _write_config(config_path, source)

    exit_code = main(["backup", "--config", str(config_path), "--profile", "not-configured"])

    assert exit_code == 2
    assert "unknown backup profile 'not-configured'" in capsys.readouterr().err
