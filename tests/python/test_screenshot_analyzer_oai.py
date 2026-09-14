"""Live OpenAI evaluations for screenshot classification and extraction.

The descriptive fixture corpus is committed under ``tests/fixtures/screenshot-evals``. Run with:

    uv run pytest -m oai tests/python/test_screenshot_analyzer_oai.py

Set ``SCREENSHOT_EVAL_ASSET_DIR`` only to evaluate an alternate corpus with the same layout.
"""

from __future__ import annotations

import base64
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from backend.models.screenshot import ScreenshotType
from backend.services.screenshot import (
    MIN_SCREENSHOT_CLASSIFICATION_CONFIDENCE,
    analyze_screenshot,
)

pytestmark = pytest.mark.oai

_OPENAI_CONFIGURED = bool(os.environ.get("OPENAI_API_KEY"))
_DEFAULT_ASSET_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "screenshot-evals"
_ASSET_DIR = Path(os.environ.get("SCREENSHOT_EVAL_ASSET_DIR", _DEFAULT_ASSET_DIR)).expanduser()

Data = dict[str, Any]
Validator = Callable[[Data], None]


@dataclass(frozen=True)
class ScreenshotEvalCase:
    name: str
    relative_path: str
    expected_type: ScreenshotType
    validate: Validator


def _assert_number(
    data: Data,
    field: str,
    expected: float,
    *,
    absolute_tolerance: float = 0.0,
) -> None:
    actual = data[field]
    assert isinstance(actual, int | float), f"{field} was not numeric: {actual!r}"
    assert actual == pytest.approx(expected, abs=absolute_tolerance)


def _find_observation_value(observations: list[Data], label_fragment: str) -> str | None:
    for observation in observations:
        label = str(observation.get("label", ""))
        if label_fragment.casefold() in label.casefold():
            value = observation.get("value")
            return str(value) if value is not None else None
    return None


def _observation_value(observations: list[Data], label_fragment: str) -> str:
    value = _find_observation_value(observations, label_fragment)
    assert value is not None, (
        f"No valued observation label contained {label_fragment!r}: {observations!r}"
    )
    return value


def _entry_by_date(data: Data, date_fragment: str) -> Data:
    for entry in data["entries"]:
        if date_fragment.casefold() in str(entry.get("date", "")).casefold():
            return entry
    raise AssertionError(f"No wellness entry matched {date_fragment!r}: {data['entries']!r}")


def _training_load_value(data: Data, metric: str) -> float:
    aliases = {"form": {"form", "balance", "tsb"}}
    accepted = aliases.get(metric, {metric})
    for point in data["series"]:
        point_names = {
            str(point.get("metric", "")).casefold(),
            str(point.get("label", "")).casefold(),
        }
        if point_names & accepted and point.get("value") is not None:
            return float(point["value"])

    observations = data["additional_observations"]
    for accepted_name in accepted:
        value = _find_observation_value(observations, accepted_name)
        if value is not None:
            return float(value)

    for annotation in data["visible_annotations"]:
        if any(name in annotation.casefold() for name in accepted):
            match = re.search(r"-?\d+(?:\.\d+)?", annotation)
            if match is not None:
                return float(match.group())
    raise AssertionError(f"No {metric!r} value was retained in the extraction: {data!r}")


def _validate_recipe_card(data: Data) -> None:
    observations = data["observations"]
    extracted_text = " ".join(
        f"{observation.get('label', '')} {observation.get('value', '')}"
        for observation in observations
    ).casefold()
    assert "tomato soup" in extracted_text
    assert "tomatoes" in extracted_text


def _validate_intervals_activity(data: Data) -> None:
    assert data["sport"] == "cycling"
    assert data["activity_date"] == "2026-09-12"
    _assert_number(data, "duration_seconds", 764, absolute_tolerance=1)
    _assert_number(data, "distance_meters", 4844, absolute_tolerance=25)
    _assert_number(data, "avg_hr_bpm", 147)
    _assert_number(data, "max_hr_bpm", 167)
    _assert_number(data, "avg_power_watts", 91)
    _assert_number(data, "normalized_power_watts", 118)


def _validate_surf_activity(data: Data) -> None:
    _assert_number(data, "duration_seconds", 8071, absolute_tolerance=1)
    _assert_number(data, "distance_meters", 4107, absolute_tolerance=25)
    _assert_number(data, "avg_hr_bpm", 101)
    _assert_number(data, "max_hr_bpm", 161)
    assert data["elevation_gain_meters"] is None
    assert "412" in _observation_value(data["additional_observations"], "surf distance")


def _validate_garmin_ride_overview(data: Data) -> None:
    assert data["sport"] == "cycling"
    assert data["activity_date"] == "2026-08-26"
    _assert_number(data, "duration_seconds", 4266, absolute_tolerance=1)
    _assert_number(data, "distance_meters", 21630, absolute_tolerance=50)
    _assert_number(data, "elevation_gain_meters", 176, absolute_tolerance=2)
    _assert_number(data, "avg_hr_bpm", 152)


def _validate_garmin_ride_details(data: Data) -> None:
    # This crop contains workout statistics but no date. The model may infer cycling
    # from the cycling-specific metrics or abstain with null/general.
    assert data["sport"] in {None, "general", "cycling"}
    assert data["activity_date"] is None
    _assert_number(data, "duration_seconds", 4266, absolute_tolerance=1)
    _assert_number(data, "distance_meters", 21630, absolute_tolerance=50)
    _assert_number(data, "elevation_gain_meters", 176, absolute_tolerance=2)
    _assert_number(data, "avg_hr_bpm", 152)
    _assert_number(data, "max_hr_bpm", 182)


def _validate_strava_ride(data: Data) -> None:
    assert data["sport"] == "cycling"
    assert data["activity_date"] == "2026-09-12"
    _assert_number(data, "duration_seconds", 764, absolute_tolerance=1)
    _assert_number(data, "distance_meters", 4844, absolute_tolerance=25)
    _assert_number(data, "avg_hr_bpm", 147)
    _assert_number(data, "max_hr_bpm", 167)
    _assert_number(data, "avg_power_watts", 96)


def _validate_strava_run_summary(data: Data) -> None:
    assert data["sport"] == "running"
    assert data["activity_date"] == "2026-08-18"
    _assert_number(data, "duration_seconds", 3207, absolute_tolerance=1)
    _assert_number(data, "distance_meters", 7500, absolute_tolerance=50)
    _assert_number(data, "elevation_gain_meters", 96, absolute_tolerance=2)
    _assert_number(data, "avg_pace_sec_per_km", 428, absolute_tolerance=5)


def _validate_strava_run_zones(data: Data) -> None:
    assert data["sport"] == "running"
    assert data["activity_date"] is None
    _assert_number(data, "duration_seconds", 3207, absolute_tolerance=1)
    _assert_number(data, "distance_meters", 7403, absolute_tolerance=50)
    _assert_number(data, "avg_pace_sec_per_km", 428, absolute_tolerance=5)
    assert "35:28" in _observation_value(data["additional_observations"], "Z2")


def _validate_hrv(data: Data) -> None:
    assert len(data["entries"]) == 7
    for day in range(7, 14):
        _entry_by_date(data, f"Sep {day}")
    assert (
        _observation_value(_entry_by_date(data, "Sep 7")["additional_observations"], "HRV status")
        == "Low"
    )
    assert (
        _observation_value(_entry_by_date(data, "Sep 13")["additional_observations"], "HRV status")
        == "Unbalanced"
    )
    # The chart does not label exact point values. The model may abstain; if it elects
    # to retain a visually estimated point, keep it within the chart's visible scale.
    estimated_values = [entry["hrv_ms"] for entry in data["entries"] if entry["hrv_ms"] is not None]
    assert all(25 <= value <= 36 for value in estimated_values)


def _validate_sleep(data: Data) -> None:
    assert len(data["entries"]) == 7
    sep_13 = _entry_by_date(data, "Sep 13")
    _assert_number(sep_13, "sleep_score", 83)
    _assert_number(sep_13, "sleep_duration_hours", 9.97, absolute_tolerance=0.02)
    sep_12 = _entry_by_date(data, "Sep 12")
    _assert_number(sep_12, "sleep_score", 91)
    _assert_number(sep_12, "sleep_duration_hours", 10.6, absolute_tolerance=0.02)
    sep_11 = _entry_by_date(data, "Sep 11")
    _assert_number(sep_11, "sleep_score", 59)
    _assert_number(sep_11, "sleep_duration_hours", 18.37, absolute_tolerance=0.02)


def _validate_ariduck_load(data: Data) -> None:
    assert _training_load_value(data, "fitness") == pytest.approx(21)
    assert _training_load_value(data, "fatigue") == pytest.approx(11)
    assert _training_load_value(data, "form") == pytest.approx(10)


def _validate_intervals_load(data: Data) -> None:
    assert _training_load_value(data, "fitness") == pytest.approx(34)
    assert _training_load_value(data, "fatigue") == pytest.approx(16)
    assert _training_load_value(data, "form") == pytest.approx(18)


_CASES = [
    ScreenshotEvalCase(
        "recipe-card", "unknown/tomato-soup-recipe-card.png", "unknown", _validate_recipe_card
    ),
    ScreenshotEvalCase(
        "intervals-activity",
        "activity/intervals-activity-analysis.png",
        "activity_single",
        _validate_intervals_activity,
    ),
    ScreenshotEvalCase(
        "surf-activity",
        "activity/garmin-surf-activity-stats.png",
        "activity_single",
        _validate_surf_activity,
    ),
    ScreenshotEvalCase(
        "garmin-ride-overview",
        "activity/garmin-cycling-activity-overview.png",
        "activity_single",
        _validate_garmin_ride_overview,
    ),
    ScreenshotEvalCase(
        "garmin-ride-details",
        "activity/garmin-cycling-activity-stats.png",
        "activity_single",
        _validate_garmin_ride_details,
    ),
    ScreenshotEvalCase(
        "strava-ride",
        "activity/strava-cycling-activity-overview.png",
        "activity_single",
        _validate_strava_ride,
    ),
    ScreenshotEvalCase(
        "strava-run-summary",
        "activity/strava-running-activity-overview.png",
        "activity_single",
        _validate_strava_run_summary,
    ),
    ScreenshotEvalCase(
        "strava-run-zones",
        "activity/strava-running-heart-rate-zones.png",
        "activity_single",
        _validate_strava_run_zones,
    ),
    ScreenshotEvalCase(
        "hrv",
        "hrv/garmin-hrv-seven-day-chart.png",
        "wellness_multi_day",
        _validate_hrv,
    ),
    ScreenshotEvalCase(
        "sleep",
        "sleep/garmin-sleep-seven-day-table.png",
        "wellness_multi_day",
        _validate_sleep,
    ),
    ScreenshotEvalCase(
        "ariduck-chart",
        "training-load/ariduck-training-balance-chart.png",
        "training_load_chart",
        _validate_ariduck_load,
    ),
    ScreenshotEvalCase(
        "ariduck-values",
        "training-load/ariduck-training-balance-values.png",
        "training_load_chart",
        _validate_ariduck_load,
    ),
    ScreenshotEvalCase(
        "intervals-chart",
        "training-load/intervals-fitness-fatigue-form-chart.png",
        "training_load_chart",
        _validate_intervals_load,
    ),
    ScreenshotEvalCase(
        "intervals-values",
        "training-load/intervals-fitness-fatigue-form-values.png",
        "training_load_chart",
        _validate_intervals_load,
    ),
]


def _asset_path(case: ScreenshotEvalCase) -> Path:
    path = _ASSET_DIR / case.relative_path
    assert path.is_file(), f"Missing screenshot eval fixture: {path}"
    return path


def _image_data_url(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def test_screenshot_eval_manifest_covers_every_png() -> None:
    catalogued = {_asset_path(case).resolve() for case in _CASES}
    available = {path.resolve() for path in _ASSET_DIR.rglob("*.png")}
    assert catalogued == available


@pytest.mark.skipif(not _OPENAI_CONFIGURED, reason="OPENAI_API_KEY is required.")
@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.name)
async def test_live_openai_screenshot_classification_and_extraction(
    case: ScreenshotEvalCase,
) -> None:
    result = await analyze_screenshot(_image_data_url(_asset_path(case)))

    assert result.screenshot_type == case.expected_type
    classification = result.data["classification"]
    assert classification["screenshot_type"] == case.expected_type
    if case.expected_type != "unknown":
        assert classification["confidence"] >= MIN_SCREENSHOT_CLASSIFICATION_CONFIDENCE
    case.validate(result.data)
