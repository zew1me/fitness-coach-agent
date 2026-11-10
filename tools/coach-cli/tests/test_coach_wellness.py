from __future__ import annotations

import json
from typing import Any

import pytest
from fitness_coach_cli.errors import CoachError
from fitness_coach_cli.wellness import map_sidecar_rows, parse_sidecar_json

# Shape produced by `garmin-sidecar wellness export --format json` (newest first; absent
# metrics omitted; a measured zero retained; floats where the source value is fractional).
SIDECAR_SAMPLE = [
    {
        "log_date": "2026-08-15",
        "sleep_duration_hours": 7.4,
        "sleep_score": 82,
        "sleep_consistency_pct": 78,
        "hrv_ms": 44,
        "resting_hr_bpm": 49,
        "body_battery": 78,
        "stress_score": 28,
    },
    {
        "log_date": "2026-08-14",
        "sleep_duration_hours": 5.9,
        "sleep_score": 61.0,
        "hrv_ms": 31,
        "resting_hr_bpm": 54,
        "body_battery": 0,
        "stress_score": 40.6,
    },
    {"log_date": "2026-08-13"},
]


def test_maps_full_and_partial_rows_with_garmin_source() -> None:
    result = map_sidecar_rows(SIDECAR_SAMPLE)
    assert result.entries[0] == {**SIDECAR_SAMPLE[0], "source": "garmin"}
    second = result.entries[1]
    assert second["source"] == "garmin"
    assert second["sleep_score"] == 61
    assert isinstance(second["sleep_score"], int)
    assert second["stress_score"] == 41
    assert second["body_battery"] == 0  # a measured zero is kept
    assert "sleep_consistency_pct" not in second  # absent stays absent, never null


def test_row_with_no_measurements_is_skipped() -> None:
    result = map_sidecar_rows(SIDECAR_SAMPLE)
    assert len(result.entries) == 2
    assert result.skipped_dates == ["2026-08-13"]


def test_unknown_and_non_numeric_fields_are_dropped_and_reported() -> None:
    rows: list[Any] = [
        {"log_date": "2026-08-15", "hrv_ms": "n/a", "respiration": 14, "sleep_score": 70}
    ]
    result = map_sidecar_rows(rows)
    assert result.entries == [{"log_date": "2026-08-15", "source": "garmin", "sleep_score": 70}]
    assert result.dropped_fields == {"hrv_ms", "respiration"}


@pytest.mark.parametrize(
    "payload",
    ['{"log_date": "2026-08-15"}', "[1, 2]", '[{"hrv_ms": 4}]', '[{"log_date": "soon"}]', "nope"],
)
def test_invalid_input_is_rejected(payload: str) -> None:
    with pytest.raises(CoachError) as info:
        parse_sidecar_json(payload)
    assert info.value.error == "invalid_wellness_input"


def test_parse_roundtrip() -> None:
    assert len(parse_sidecar_json(json.dumps(SIDECAR_SAMPLE)).entries) == 2
