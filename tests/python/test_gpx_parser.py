from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path

import pytest

from backend.engine.gpx_parser import ParsedActivity, ParsedCourse, parse_fit


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


def _fit_message(**fields: object) -> _FakeMessage:
    """Build any FIT message — session, course, lap, record, or file_id."""
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
        _fit_message(
            sport="cycling",
            total_elapsed_time=5880.208,
            total_timer_time=5880.208,
        ),
    ]

    activity = parse_fit(tmp_path / "ride.fit")

    assert isinstance(activity, ParsedActivity)

    assert activity.sport == "cycling"
    assert activity.duration_seconds == 5880
    assert activity.elapsed_duration_seconds == 5880
    assert activity.moving_duration_seconds == 5880


def test_parse_fit_prefers_moving_time_when_elapsed_and_moving_differ(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    _patch_fitparse["session"] = [
        _fit_message(
            sport="cycling",
            total_elapsed_time=4000,
            total_timer_time=3600,
        ),
    ]

    activity = parse_fit(tmp_path / "ride.fit")

    assert isinstance(activity, ParsedActivity)

    assert activity.duration_seconds == 3600
    assert activity.elapsed_duration_seconds == 4000
    assert activity.moving_duration_seconds == 3600


def test_parse_fit_sums_durations_across_multiple_sessions(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    _patch_fitparse["session"] = [
        _fit_message(
            sport="running",
            total_elapsed_time=1800,
            total_timer_time=1800,
        ),
        _fit_message(
            sport="cycling",
            total_elapsed_time=3600,
            total_timer_time=3600,
        ),
    ]

    activity = parse_fit(tmp_path / "brick.fit")

    assert isinstance(activity, ParsedActivity)

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
        _fit_message(
            sport="running",
            start_time=second_start,
            total_distance=10000,
            total_ascent=150,
        ),
        _fit_message(
            sport="cycling",
            start_time=first_start,
            total_distance=40000,
            total_ascent=350,
        ),
    ]

    activity = parse_fit(tmp_path / "brick.fit")

    assert isinstance(activity, ParsedActivity)

    assert activity.started_at == first_start
    assert activity.distance_meters == 50000
    assert activity.elevation_gain_meters == 500


def test_parse_fit_handles_missing_timer_time_falls_back_to_elapsed(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    _patch_fitparse["session"] = [
        _fit_message(
            sport="running",
            total_elapsed_time=1200,
        ),
    ]

    activity = parse_fit(tmp_path / "run.fit")

    assert isinstance(activity, ParsedActivity)

    assert activity.duration_seconds == 1200
    assert activity.elapsed_duration_seconds == 1200
    assert activity.moving_duration_seconds is None


@pytest.mark.parametrize("timestamp_tz", [UTC, None])
def test_parse_fit_uses_activity_local_timestamp_for_activity_date(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
    timestamp_tz: tzinfo | None,
) -> None:
    start_time = datetime(2026, 7, 6, 3, 31, 48, tzinfo=timestamp_tz)
    _patch_fitparse["session"] = [_fit_message(sport="cycling", start_time=start_time)]
    _patch_fitparse["activity"] = [
        _fit_message(
            timestamp=datetime(2026, 7, 6, 3, 31, 48, tzinfo=timestamp_tz),
            local_timestamp=datetime(2026, 7, 5, 20, 31, 48, tzinfo=timestamp_tz),
        )
    ]

    activity = parse_fit(tmp_path / "ride.fit")

    assert isinstance(activity, ParsedActivity)

    assert activity.activity_date.isoformat() == "2026-07-05"
    assert activity.utc_offset_seconds == -25200


def test_parse_fit_normalizes_aware_session_start_before_local_offset(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    source_timezone = timezone(timedelta(hours=5, minutes=30))
    _patch_fitparse["session"] = [
        _fit_message(
            sport="cycling",
            start_time=datetime(2026, 7, 6, 9, 1, 48, tzinfo=source_timezone),
        )
    ]
    _patch_fitparse["activity"] = [
        _fit_message(
            timestamp=datetime(2026, 7, 6, 9, 1, 48, tzinfo=source_timezone),
            local_timestamp=datetime(2026, 7, 5, 20, 31, 48, tzinfo=UTC),
        )
    ]

    activity = parse_fit(tmp_path / "ride.fit")

    assert isinstance(activity, ParsedActivity)

    assert activity.activity_date.isoformat() == "2026-07-05"
    assert activity.utc_offset_seconds == -25200


def test_parse_fit_without_activity_message_keeps_utc_date(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    _patch_fitparse["session"] = [
        _fit_message(sport="cycling", start_time=datetime(2026, 7, 6, 3, 31, 48, tzinfo=UTC))
    ]

    activity = parse_fit(tmp_path / "ride.fit")

    assert isinstance(activity, ParsedActivity)

    assert activity.activity_date.isoformat() == "2026-07-06"
    assert activity.utc_offset_seconds is None


def test_parse_fit_rejects_out_of_range_activity_utc_offset(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    start_time = datetime(2026, 7, 6, 3, 31, 48, tzinfo=UTC)
    _patch_fitparse["session"] = [_fit_message(sport="cycling", start_time=start_time)]
    _patch_fitparse["activity"] = [
        _fit_message(
            timestamp=start_time,
            local_timestamp=datetime(2026, 7, 6, 18, 31, 48, tzinfo=UTC),
        )
    ]

    activity = parse_fit(tmp_path / "ride.fit")

    assert isinstance(activity, ParsedActivity)

    assert activity.activity_date.isoformat() == "2026-07-06"
    assert activity.utc_offset_seconds is None


def test_parse_fit_preserves_zero_moving_duration(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    _patch_fitparse["session"] = [
        _fit_message(
            sport="cycling",
            total_elapsed_time=1200,
            total_timer_time=0,
        ),
    ]

    activity = parse_fit(tmp_path / "ride.fit")

    assert isinstance(activity, ParsedActivity)

    assert activity.duration_seconds == 0
    assert activity.elapsed_duration_seconds == 1200
    assert activity.moving_duration_seconds == 0


# --- FIT course files ----------------------------------------------------------
#
# Unlike GPX, a FIT says outright which it is: a course file carries a `course`
# message and no `session`. Its timestamps, where present, are synthetic, so
# absence-of-time is the wrong test here.


def test_parse_fit_course_message_yields_a_course_not_an_activity(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    _patch_fitparse["course"] = [_fit_message(sport="cycling", name="Schotterfest Long")]
    _patch_fitparse["lap"] = [_fit_message(total_distance=94490.2, total_ascent=2308.6)]
    _patch_fitparse["record"] = [
        _fit_message(distance=0.0, altitude=100.0),
        _fit_message(distance=1000.0, altitude=180.0),
    ]

    course = parse_fit(tmp_path / "course.fit")

    assert isinstance(course, ParsedCourse)
    assert course.sport == "cycling"
    assert course.name == "Schotterfest Long"
    # Lap totals are authoritative; the record stream only supplies grades.
    assert course.profile.distance_meters == 94490.2
    assert course.profile.elevation_gain_meters == 2308.6
    assert course.profile.avg_grade_pct == 8.0


def test_parse_fit_course_without_records_omits_grades_rather_than_faking_them(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    _patch_fitparse["course"] = [_fit_message(sport="cycling")]
    _patch_fitparse["lap"] = [_fit_message(total_distance=94490.2, total_ascent=2308.6)]

    course = parse_fit(tmp_path / "course.fit")

    assert isinstance(course, ParsedCourse)
    assert course.profile.distance_meters == 94490.2
    assert course.profile.elevation_gain_meters == 2308.6
    assert course.profile.avg_grade_pct is None
    assert course.profile.max_grade_pct is None


def test_parse_fit_course_with_unmappable_sport_reports_unknown(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    _patch_fitparse["course"] = [_fit_message(sport="generic")]
    _patch_fitparse["lap"] = [_fit_message(total_distance=5000.0)]

    course = parse_fit(tmp_path / "course.fit")

    assert isinstance(course, ParsedCourse)
    assert course.sport == "general"


def test_parse_fit_prefers_the_session_when_a_file_carries_both(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    # A recorded ride that also embeds the course it followed is still a recording.
    _patch_fitparse["course"] = [_fit_message(sport="cycling", name="Followed course")]
    _patch_fitparse["session"] = [
        _fit_message(sport="cycling", total_elapsed_time=3600, total_timer_time=3400),
    ]

    activity = parse_fit(tmp_path / "ride.fit")

    assert isinstance(activity, ParsedActivity)

    assert activity.duration_seconds == 3400


def test_parse_fit_prefers_enhanced_altitude_when_present(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    # Garmin writes both; `enhanced_altitude` is the higher-resolution field.
    _patch_fitparse["course"] = [_fit_message(sport="running")]
    _patch_fitparse["lap"] = [_fit_message(total_distance=1000.0)]
    _patch_fitparse["record"] = [
        _fit_message(distance=0.0, altitude=100.0, enhanced_altitude=100.0),
        _fit_message(distance=500.0, altitude=120.0, enhanced_altitude=150.0),
    ]

    course = parse_fit(tmp_path / "course.fit")

    assert isinstance(course, ParsedCourse)
    assert course.profile.avg_grade_pct == 10.0


def test_parse_fit_file_id_declares_a_course_without_a_course_message(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    # file_id.type is the file's own declaration of what it is, and is the stronger
    # signal. Keying only on the `course` message meant a course file that omitted it
    # was persisted as an activity dated today — the exact issue-243 symptom.
    _patch_fitparse["file_id"] = [_fit_message(type="course")]
    _patch_fitparse["lap"] = [_fit_message(total_distance=42000.0, total_ascent=900.0)]

    course = parse_fit(tmp_path / "course.fit")

    assert isinstance(course, ParsedCourse)
    assert course.profile.distance_meters == 42000.0
    assert course.profile.elevation_gain_meters == 900.0
    # No course message means no declared sport to read.
    assert course.sport == "general"


def test_parse_fit_activity_file_id_is_not_treated_as_a_course(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    _patch_fitparse["file_id"] = [_fit_message(type="activity")]
    _patch_fitparse["session"] = [
        _fit_message(sport="cycling", total_elapsed_time=3600, total_timer_time=3400),
    ]

    activity = parse_fit(tmp_path / "ride.fit")

    assert isinstance(activity, ParsedActivity)
    assert activity.duration_seconds == 3400


def test_parse_fit_course_record_stream_starting_part_way_does_not_invent_distance(
    _patch_fitparse: dict[str, list[_FakeMessage]],
    tmp_path: Path,
) -> None:
    # The first record with a distance anchors the scale rather than travelling.
    # Starting the baseline at 0.0 charged its whole cumulative reading as a step, so
    # a stream opening part-way along the route invented that much distance.
    _patch_fitparse["course"] = [_fit_message(sport="cycling")]
    _patch_fitparse["record"] = [
        _fit_message(distance=5000.0, altitude=100.0),
        _fit_message(distance=6000.0, altitude=180.0),
    ]

    course = parse_fit(tmp_path / "course.fit")

    assert isinstance(course, ParsedCourse)
    # 1000 m of travel, not 6000.
    assert course.profile.distance_meters == 1000.0
    assert course.profile.avg_grade_pct == 8.0
