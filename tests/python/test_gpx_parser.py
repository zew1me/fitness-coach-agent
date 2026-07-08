from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.engine.gpx_parser import parse_fit


class _FakeField:
    def __init__(self, name: str, value: object) -> None:
        self.name = name
        self.value = value


class _FakeMessage:
    def __init__(self, fields: list[_FakeField]) -> None:
        self.fields = fields


class _FakeFitFile:
    def __init__(self, messages_by_type: dict[str, list[_FakeMessage]]) -> None:
        self._messages_by_type = messages_by_type

    def get_messages(self, name: str) -> list[_FakeMessage]:
        return self._messages_by_type.get(name, [])


def _session_message(**fields: object) -> _FakeMessage:
    return _FakeMessage([_FakeField(name, value) for name, value in fields.items()])


@pytest.fixture(autouse=True)
def _patch_fitparse(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[_FakeMessage]]:
    messages_by_type: dict[str, list[_FakeMessage]] = {}

    import fitparse

    monkeypatch.setattr(
        fitparse,
        "FitFile",
        lambda _path: _FakeFitFile(messages_by_type),
    )
    return messages_by_type


def test_parse_fit_extracts_elapsed_and_moving_duration_single_session(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    _patch_fitparse["session"] = [
        _session_message(
            sport="cycling",
            total_elapsed_time=5880.208,
            total_timer_time=5880.208,
        ),
    ]

    activity = parse_fit(tmp_path / "ride.fit")

    assert activity.sport == "cycling"
    assert activity.duration_seconds == 5880
    assert activity.elapsed_duration_seconds == 5880
    assert activity.moving_duration_seconds == 5880


def test_parse_fit_prefers_moving_time_when_elapsed_and_moving_differ(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    _patch_fitparse["session"] = [
        _session_message(
            sport="cycling",
            total_elapsed_time=4000,
            total_timer_time=3600,
        ),
    ]

    activity = parse_fit(tmp_path / "ride.fit")

    assert activity.duration_seconds == 3600
    assert activity.elapsed_duration_seconds == 4000
    assert activity.moving_duration_seconds == 3600


def test_parse_fit_sums_durations_across_multiple_sessions(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    _patch_fitparse["session"] = [
        _session_message(
            sport="running",
            total_elapsed_time=1800,
            total_timer_time=1800,
        ),
        _session_message(
            sport="cycling",
            total_elapsed_time=3600,
            total_timer_time=3600,
        ),
    ]

    activity = parse_fit(tmp_path / "brick.fit")

    # Regression test for the last-one-wins overwrite bug: the correct total
    # is the sum across sessions (5400), not just the last session's value
    # (3600).
    assert activity.duration_seconds == 5400
    assert activity.elapsed_duration_seconds == 5400
    assert activity.moving_duration_seconds == 5400


def test_parse_fit_sums_distance_and_ascent_across_multiple_sessions(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    first_start = datetime(2026, 7, 7, 6, 30, tzinfo=UTC)
    second_start = datetime(2026, 7, 7, 7, 10, tzinfo=UTC)
    _patch_fitparse["session"] = [
        _session_message(
            sport="running",
            start_time=second_start,
            total_distance=10000,
            total_ascent=150,
        ),
        _session_message(
            sport="cycling",
            start_time=first_start,
            total_distance=40000,
            total_ascent=350,
        ),
    ]

    activity = parse_fit(tmp_path / "brick.fit")

    assert activity.started_at == first_start
    assert activity.distance_meters == 50000
    assert activity.elevation_gain_meters == 500


def test_parse_fit_handles_missing_timer_time_falls_back_to_elapsed(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    _patch_fitparse["session"] = [
        _session_message(
            sport="running",
            total_elapsed_time=1200,
        ),
    ]

    activity = parse_fit(tmp_path / "run.fit")

    assert activity.duration_seconds == 1200
    assert activity.elapsed_duration_seconds == 1200
    assert activity.moving_duration_seconds is None


def test_parse_fit_preserves_zero_moving_duration(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    _patch_fitparse["session"] = [
        _session_message(
            sport="cycling",
            total_elapsed_time=1200,
            total_timer_time=0,
        ),
    ]

    activity = parse_fit(tmp_path / "ride.fit")

    assert activity.duration_seconds == 0
    assert activity.elapsed_duration_seconds == 1200
    assert activity.moving_duration_seconds == 0
