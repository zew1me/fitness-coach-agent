from __future__ import annotations

import io
import stat
import zipfile
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest
from fitness_coach_garmin_sidecar import cli as garmin_connect
from garminconnect import GarminConnectConnectionError
from typer.testing import CliRunner


def _fit_bytes(marker: bytes = b"") -> bytes:
    return bytes([14, 0x10, 0, 0, 0, 0, 0, 0]) + b".FIT" + b"\x00\x00" + marker


def _original_archive(*entries: tuple[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, payload in entries:
            archive.writestr(name, payload)
    return output.getvalue()


class FakeGarminClient:
    def __init__(
        self,
        activities: list[dict[str, Any]],
        originals: dict[str, bytes | Exception],
    ) -> None:
        self.activities = activities
        self.originals = originals
        self.date_calls: list[tuple[str, str | None, str | None]] = []
        self.download_calls: list[str] = []

    def get_activities_by_date(
        self,
        startdate: str,
        enddate: str | None = None,
        activitytype: str | None = None,
        sortorder: str | None = None,
    ) -> list[dict[str, Any]]:
        self.date_calls.append((startdate, enddate, sortorder))
        return self.activities

    def download_activity(self, activity_id: str, dl_fmt: Any = None) -> bytes:
        self.download_calls.append(activity_id)
        result = self.originals[activity_id]
        if isinstance(result, Exception):
            raise result
        return result


def test_parse_date_window_is_inclusive_and_strict() -> None:
    assert garmin_connect.parse_date_window("2026-07-01", "2026-07-31") == (
        date(2026, 7, 1),
        date(2026, 7, 31),
    )

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        garmin_connect.parse_date_window("07/01/2026", "2026-07-31")
    with pytest.raises(ValueError, match="on or before"):
        garmin_connect.parse_date_window("2026-08-01", "2026-07-31")


def test_fit_payload_from_original_accepts_fit_or_single_fit_zip() -> None:
    fit = _fit_bytes(b"activity")

    assert garmin_connect.fit_payload_from_original(fit) == fit
    assert (
        garmin_connect.fit_payload_from_original(
            _original_archive(("nested/activity.FIT", fit), ("metadata.json", b"{}"))
        )
        == fit
    )


@pytest.mark.parametrize(
    ("archive", "message"),
    [
        (b"not an archive", "neither a FIT file nor a valid ZIP"),
        (_original_archive(("activity.gpx", b"gpx")), "contains no FIT"),
        (
            _original_archive(("one.fit", _fit_bytes()), ("two.fit", _fit_bytes())),
            "contains 2 FIT files",
        ),
        (_original_archive(("activity.fit", b"not fit")), "valid FIT header"),
    ],
)
def test_fit_payload_from_original_rejects_ambiguous_or_invalid_downloads(
    archive: bytes, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        garmin_connect.fit_payload_from_original(archive)


def test_download_fit_activities_is_idempotent_private_and_partial_failure_safe(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "101.fit"
    existing.write_bytes(_fit_bytes(b"existing"))
    client = FakeGarminClient(
        activities=[{"activityId": 101}, {"activityId": 202}, {"activityId": 303}],
        originals={
            "101": _original_archive(("101.fit", _fit_bytes(b"replacement"))),
            "202": _original_archive(("activity.fit", _fit_bytes(b"downloaded"))),
            "303": ValueError("bad Garmin archive"),
        },
    )

    summary = garmin_connect.download_fit_activities(
        client,
        start=date(2026, 7, 1),
        end=date(2026, 7, 31),
        output_dir=tmp_path,
    )

    assert client.date_calls == [("2026-07-01", "2026-07-31", "asc")]
    assert client.download_calls == ["202", "303"]
    assert summary.skipped == [existing]
    assert summary.downloaded == [tmp_path / "202.fit"]
    assert [(failure.activity_id, failure.message) for failure in summary.failures] == [
        ("303", "bad Garmin archive")
    ]
    assert existing.read_bytes() == _fit_bytes(b"existing")
    assert (tmp_path / "202.fit").read_bytes() == _fit_bytes(b"downloaded")
    assert stat.S_IMODE((tmp_path / "202.fit").stat().st_mode) == 0o600


def test_download_fit_activities_overwrites_only_when_requested(tmp_path: Path) -> None:
    target = tmp_path / "101.fit"
    target.write_bytes(_fit_bytes(b"old"))
    replacement = _fit_bytes(b"new")
    client = FakeGarminClient(
        activities=[{"activityId": 101}],
        originals={"101": _original_archive(("activity.fit", replacement))},
    )

    summary = garmin_connect.download_fit_activities(
        client,
        start=date(2026, 7, 1),
        end=date(2026, 7, 1),
        output_dir=tmp_path,
        overwrite=True,
    )

    assert summary.downloaded == [target]
    assert target.read_bytes() == replacement


def test_download_fit_activities_rejects_unsafe_activity_id_and_continues(
    tmp_path: Path,
) -> None:
    client = FakeGarminClient(
        activities=[{"activityId": "../../secret"}, {"activityId": 202}],
        originals={"202": _original_archive(("activity.fit", _fit_bytes()))},
    )

    summary = garmin_connect.download_fit_activities(
        client,
        start=date(2026, 7, 1),
        end=date(2026, 7, 1),
        output_dir=tmp_path,
    )

    assert client.download_calls == ["202"]
    assert summary.downloaded == [tmp_path / "202.fit"]
    assert summary.failures[0].activity_id == "../../secret"
    assert "invalid or missing" in summary.failures[0].message


def test_authenticate_prompts_hidden_password_then_discards_it_and_secures_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompts: list[tuple[str, bool]] = []

    def prompt(label: str, *, hide_input: bool = False) -> str:
        prompts.append((label, hide_input))
        return "secret-password" if hide_input else "athlete@example.com"

    class FreshGarmin:
        def __init__(
            self,
            email: str,
            password: str,
            prompt_mfa: Any,
        ) -> None:
            self.email = email
            self.password: str | None = password
            self.prompt_mfa = prompt_mfa

        def login(self, token_store: str) -> None:
            path = Path(token_store)
            path.mkdir(exist_ok=True)
            (path / "garmin_tokens.json").write_text("{}")

    monkeypatch.setattr(garmin_connect, "Garmin", FreshGarmin)
    monkeypatch.setattr(garmin_connect.typer, "prompt", prompt)

    client = cast(Any, garmin_connect.authenticate(token_store=tmp_path, email=None))

    assert client.email == "athlete@example.com"
    assert client.password is None
    assert prompts == [("Garmin email", False), ("Garmin password", True)]
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "garmin_tokens.json").stat().st_mode) == 0o600


def test_authenticate_does_not_prompt_fresh_login_on_cached_connection_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_file = tmp_path / "garmin_tokens.json"
    token_file.write_text("{}")

    class FailingCachedGarmin:
        def login(self, _token_store: str) -> None:
            raise GarminConnectConnectionError("Garmin is unavailable")

    def unexpected_prompt(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("credential prompt must not run")

    monkeypatch.setattr(garmin_connect, "Garmin", FailingCachedGarmin)
    monkeypatch.setattr(garmin_connect.typer, "prompt", unexpected_prompt)

    with pytest.raises(GarminConnectConnectionError, match="unavailable"):
        garmin_connect.authenticate(token_store=tmp_path, email=None)


def test_cli_reports_partial_failure_with_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    downloaded = tmp_path / "101.fit"
    skipped = tmp_path / "202.fit"

    monkeypatch.setattr(garmin_connect, "authenticate", lambda **_kwargs: object())
    monkeypatch.setattr(
        garmin_connect,
        "download_fit_activities",
        lambda *_args, **_kwargs: garmin_connect.DownloadSummary(
            downloaded=[downloaded],
            skipped=[skipped],
            failures=[garmin_connect.ActivityFailure("303", "corrupt archive")],
        ),
    )

    result = CliRunner().invoke(
        garmin_connect.app,
        ["download", "2026-07-01", "2026-07-31", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert f"downloaded {downloaded}" in result.output
    assert "failed 303: corrupt archive" in result.output
    assert "Summary: 1 downloaded, 1 skipped, 1 failed." in result.output


def test_cli_validates_window_before_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_authentication(**_kwargs: Any) -> None:
        raise AssertionError("authentication must not run")

    monkeypatch.setattr(garmin_connect, "authenticate", unexpected_authentication)

    result = CliRunner().invoke(
        garmin_connect.app,
        ["download", "2026-08-01", "2026-07-01"],
    )

    assert result.exit_code == 2
    assert "start date must be on or before end date" in result.output
