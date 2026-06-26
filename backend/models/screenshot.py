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


class ConfidenceEntry(BaseModel):
    """Per-field extraction confidence. A list of these replaces a free-form
    {field: score} map, which strict structured outputs cannot represent."""

    field: str
    confidence: float = Field(ge=0.0, le=1.0)


class ScreenshotClassificationModel(BaseModel):
    screenshot_type: ScreenshotType
    source_app_hint: str | None = None
    date_range_hint: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class ActivityExtraction(BaseModel):
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
    confidence: list[ConfidenceEntry] = Field(default_factory=list)


class WellnessDayEntry(BaseModel):
    date: str | None = None
    sleep_duration_hours: float | None = None
    sleep_score: int | None = None
    hrv_ms: float | None = None
    resting_hr_bpm: int | None = None
    body_battery: int | None = None
    stress_score: int | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class WellnessMultiExtraction(BaseModel):
    entries: list[WellnessDayEntry] = Field(default_factory=list)


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
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


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
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class TrainingLoadChartExtraction(BaseModel):
    date_range: ChartDateRange | None = None
    source_app_hint: str | None = None
    x_axis_label: str | None = None
    y_axis_label: str | None = None
    series: list[TrainingLoadPoint] = Field(default_factory=list)
    visible_annotations: list[str] = Field(default_factory=list)


class GenericObservation(BaseModel):
    """A single label/value datum read off a screenshot we have no typed schema for."""

    label: str
    value: str | None = None


class GenericExtraction(BaseModel):
    """Catch-all capture for screenshots without a specialized schema (plans, calendars,
    or unclassifiable images). Strict structured outputs cannot emit a truly open object,
    so we hand the lead coach a summary plus free-form label/value observations to mine."""

    summary: str | None = None
    observations: list[GenericObservation] = Field(default_factory=list)


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
