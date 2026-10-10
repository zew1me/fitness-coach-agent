"""Pydantic response schemas and result dataclasses for screenshot extraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

ScreenshotType = Literal[
    "activity_single",
    "wellness_multi_day",
    "wellness_single_day",
    "training_load_chart",
    "plan_or_calendar",
    "unknown",
]


class GenericObservation(BaseModel):
    """A single label/value datum read off a screenshot we have no typed schema for."""

    label: str
    value: str | None = None


class ScreenshotClassificationModel(BaseModel):
    screenshot_type: ScreenshotType
    source_app_hint: str | None = None
    date_range_hint: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class DisplayedDate(BaseModel):
    """Calendar components read from a displayed date."""

    year: int | None = Field(default=None, ge=1, le=9999)
    month: int | None = Field(default=None, ge=1, le=12)
    day: int | None = Field(default=None, ge=1, le=31)


class DisplayedDistance(BaseModel):
    """A distance exactly as displayed, before deterministic unit conversion."""

    value: float | None = None
    unit: Literal["meters", "kilometers", "feet", "yards", "miles"] | None = None


class DisplayedDuration(BaseModel):
    """A duration exactly as displayed, including its clock/numeric unit."""

    value: str | None = None
    unit: Literal["h:mm:ss", "m:ss", "seconds", "minutes", "hours"] | None = None


class DisplayedPace(BaseModel):
    """A pace exactly as displayed; application code converts it to seconds/km."""

    value: str | None = None
    unit: Literal["min/km", "min/mi", "sec/km", "sec/mi"] | None = None


class ActivityScreenshotExtraction(BaseModel):
    """Raw vision output. Unit-bearing fields are normalized after model execution."""

    sport: Literal["running", "cycling", "swimming", "rowing", "hiking", "general"] | None = None
    activity_date: DisplayedDate | None = None
    duration: DisplayedDuration | None = None
    distance: DisplayedDistance | None = None
    elevation_gain: DisplayedDistance | None = None
    avg_hr_bpm: int | None = None
    max_hr_bpm: int | None = None
    avg_power_watts: int | None = None
    normalized_power_watts: int | None = None
    avg_pace: DisplayedPace | None = None
    avg_cadence_rpm: int | None = None
    tss: float | None = None
    additional_observations: list[GenericObservation] = Field(default_factory=list)


class ActivityExtraction(BaseModel):
    """Stable, normalized activity payload returned to screenshot-analysis callers."""

    sport: Literal["running", "cycling", "swimming", "rowing", "hiking", "general"] | None = None
    activity_date: str | None = None
    duration_seconds: int | None = None
    distance_meters: float | None = None
    elevation_gain_meters: float | None = None
    avg_hr_bpm: int | None = None
    max_hr_bpm: int | None = None
    avg_power_watts: int | None = None
    normalized_power_watts: int | None = None
    avg_pace_sec_per_km: int | None = None
    avg_cadence_rpm: int | None = None
    tss: float | None = None
    additional_observations: list[GenericObservation] = Field(default_factory=list)


class WellnessDayEntry(BaseModel):
    date: str | None = None
    sleep_duration_hours: float | None = None
    sleep_score: int | None = None
    hrv_ms: float | None = None
    resting_hr_bpm: int | None = None
    body_battery: int | None = None
    stress_score: int | None = None
    additional_observations: list[GenericObservation] = Field(default_factory=list)


class WellnessMultiExtraction(BaseModel):
    entries: list[WellnessDayEntry] = Field(default_factory=list)
    additional_observations: list[GenericObservation] = Field(default_factory=list)


class WellnessSingleExtraction(BaseModel):
    date: str | None = None
    sleep_duration_hours: float | None = None
    sleep_score: int | None = None
    sleep_consistency_pct: float | None = None
    hrv_ms: float | None = None
    resting_hr_bpm: int | None = None
    body_battery: int | None = None
    stress_score: int | None = None
    subjective_energy: int | None = None
    additional_observations: list[GenericObservation] = Field(default_factory=list)


class ChartDateRange(BaseModel):
    start: str | None = None
    end: str | None = None


class TrainingLoadPoint(BaseModel):
    date: str | None = None
    metric: (
        Literal[
            "ctl",
            "atl",
            "tsb",
            "tss",
            "training_load",
            "fatigue",
            "fitness",
            "form",
            "other",
        ]
        | None
    ) = None
    label: str | None = None
    value: float | None = None


class TrainingLoadChartExtraction(BaseModel):
    date_range: ChartDateRange | None = None
    source_app_hint: str | None = None
    x_axis_label: str | None = None
    y_axis_label: str | None = None
    series: list[TrainingLoadPoint] = Field(default_factory=list)
    visible_annotations: list[str] = Field(default_factory=list)
    additional_observations: list[GenericObservation] = Field(default_factory=list)


class GenericExtraction(BaseModel):
    """Catch-all capture for screenshots without a specialized schema (plans, calendars,
    or unclassifiable images). Strict structured outputs cannot emit a truly open object,
    so we hand the lead coach a summary plus free-form label/value observations to mine."""

    summary: str | None = None
    observations: list[GenericObservation] = Field(default_factory=list)


class ScreenshotAnalysis(BaseModel):
    """Classification and extraction in one response.

    Classifying and extracting used to be two serialized vision calls against the same
    image, where the first call existed only to choose the second one's schema. This
    model lets one call do both: the model picks `screenshot_type` and fills the single
    matching payload field, leaving the rest null.

    The activity branch carries displayed values and units; the service normalizes it
    after the model call so downstream `data` keeps the established metric shape.
    """

    screenshot_type: ScreenshotType
    source_app_hint: str | None = None
    date_range_hint: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)

    activity: ActivityScreenshotExtraction | None = None
    wellness_multi: WellnessMultiExtraction | None = None
    wellness_single: WellnessSingleExtraction | None = None
    training_load_chart: TrainingLoadChartExtraction | None = None
    # Filled for plan_or_calendar and unknown, and as the fallback whenever the typed
    # branch the model chose came back empty.
    generic: GenericExtraction | None = None


@dataclass
class ScreenshotClassification:
    screenshot_type: ScreenshotType
    source_app_hint: str | None
    date_range_hint: str | None
    confidence: float


@dataclass
class ExtractionResult:
    screenshot_type: ScreenshotType
    data: dict[str, Any]
    raw_response: str
