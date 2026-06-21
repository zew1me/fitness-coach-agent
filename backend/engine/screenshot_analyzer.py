"""Screenshot classification and data extraction via vision model.

Two-step process:
1. Classify screenshot type (activity, wellness multi-day, wellness single-day, etc.)
2. Route to type-specific extraction prompt

Both steps use OpenAI Structured Outputs (strict `json_schema`) driven by the Pydantic
models below, so the vision model is constrained to return JSON matching our schema
rather than free-form text we have to parse defensively.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from openai import AsyncOpenAI, OpenAIError
from openai.types.shared_params import Reasoning
from pydantic import BaseModel

from backend.config import settings

logger = logging.getLogger(__name__)

MIN_SCREENSHOT_CLASSIFICATION_CONFIDENCE = 0.3

ScreenshotType = Literal[
    "activity_single",
    "wellness_multi_day",
    "wellness_single_day",
    "training_load_chart",
    "plan_or_calendar",
    "unknown",
]

CLASSIFY_PROMPT = """Analyze this screenshot and classify it into exactly one category:

- activity_single: A single workout/activity summary
  (Strava, Garmin, Runalyze, intervals.icu, Apple Fitness, etc.)
- wellness_multi_day: Multiple days of sleep/recovery/wellness data
  (sleep history, body battery trend, HRV trend)
- wellness_single_day: A single day's recovery/wellness summary
  (today's body battery, sleep score, HRV)
- training_load_chart: A fitness/fatigue chart showing CTL/ATL/TSB or similar
  training load over time
- plan_or_calendar: A training plan or workout calendar view
- unknown: Cannot determine what this screenshot shows

Provide your best source-app and date-range hints, and a confidence from 0.0 to 1.0."""

EXTRACT_ACTIVITY_PROMPT = """Extract any relevant athlete, event, or workout data shown in
this screenshot — for example a single activity/workout summary and its key metrics.
Use null for anything not clearly visible. Do not guess values. For each field you read,
add a confidence entry naming the field and your confidence from 0.0 to 1.0."""

EXTRACT_WELLNESS_MULTI_PROMPT = """Extract the daily wellness/recovery data shown in this
screenshot. It may cover multiple days — return one entry per visible day.
Use null for anything not clearly visible. Do not guess values."""

EXTRACT_WELLNESS_SINGLE_PROMPT = """Extract the day's wellness/recovery data shown in this
screenshot. Use null for anything not clearly visible. Do not guess values."""

EXTRACT_TRAINING_LOAD_CHART_PROMPT = """Extract data from this training load chart.
It may show CTL/fitness, ATL/fatigue, TSB/form, training stress, or similar time-series
lines. Capture the visible date range, axis labels, and readable value points (one series
entry per readable point). Use null when dates or labels are not visible. Approximate a
value only when the axis/grid makes it clear, and lower the confidence for approximate
points. Do not guess hidden values."""


class ConfidenceEntry(BaseModel):
    """Per-field extraction confidence. A list of these replaces a free-form
    {field: score} map, which strict structured outputs cannot represent."""

    field: str
    confidence: float


class ScreenshotClassificationModel(BaseModel):
    screenshot_type: ScreenshotType
    source_app_hint: str | None = None
    date_range_hint: str | None = None
    confidence: float


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
    confidence: list[ConfidenceEntry] = []


class WellnessDayEntry(BaseModel):
    date: str | None = None
    sleep_duration_hours: float | None = None
    sleep_score: int | None = None
    hrv_ms: float | None = None
    resting_hr_bpm: int | None = None
    body_battery: int | None = None
    stress_score: int | None = None
    confidence: float | None = None


class WellnessMultiExtraction(BaseModel):
    entries: list[WellnessDayEntry] = []


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
    confidence: float | None = None


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
    confidence: float | None = None


class TrainingLoadChartExtraction(BaseModel):
    date_range: ChartDateRange | None = None
    source_app_hint: str | None = None
    x_axis_label: str | None = None
    y_axis_label: str | None = None
    series: list[TrainingLoadPoint] = []
    visible_annotations: list[str] = []


_EXTRACTION_BY_TYPE: dict[str, tuple[str, type[BaseModel]]] = {
    "activity_single": (EXTRACT_ACTIVITY_PROMPT, ActivityExtraction),
    "training_load_chart": (EXTRACT_TRAINING_LOAD_CHART_PROMPT, TrainingLoadChartExtraction),
    "wellness_multi_day": (EXTRACT_WELLNESS_MULTI_PROMPT, WellnessMultiExtraction),
    "wellness_single_day": (EXTRACT_WELLNESS_SINGLE_PROMPT, WellnessSingleExtraction),
}


@dataclass
class ScreenshotClassification:
    screenshot_type: str
    source_app_hint: str | None
    date_range_hint: str | None
    confidence: float


@dataclass
class ExtractionResult:
    screenshot_type: str
    data: dict[str, Any]
    raw_response: str


async def classify_screenshot(image_url: str) -> ScreenshotClassification:
    """Step 1: Classify a screenshot into a category."""
    parsed = await _call_vision(CLASSIFY_PROMPT, image_url, ScreenshotClassificationModel)
    if parsed is None:
        return ScreenshotClassification(
            screenshot_type="unknown",
            source_app_hint=None,
            date_range_hint=None,
            confidence=0.0,
        )

    classification = ScreenshotClassification(
        screenshot_type=parsed.screenshot_type,
        source_app_hint=parsed.source_app_hint,
        date_range_hint=parsed.date_range_hint,
        confidence=parsed.confidence,
    )
    logger.debug(
        "screenshot classified type=%s confidence=%.2f source=%s",
        classification.screenshot_type,
        classification.confidence,
        classification.source_app_hint,
    )
    return classification


async def extract_from_screenshot(
    image_url: str,
    screenshot_type: str,
) -> ExtractionResult:
    """Step 2: Extract structured data based on classification."""
    extraction = _EXTRACTION_BY_TYPE.get(screenshot_type)
    if extraction is None:
        return ExtractionResult(
            screenshot_type=screenshot_type,
            data={},
            raw_response="Unsupported screenshot type for extraction.",
        )

    prompt, schema = extraction
    parsed = await _call_vision(prompt, image_url, schema)
    if parsed is None:
        return ExtractionResult(
            screenshot_type=screenshot_type,
            data={},
            raw_response="Vision extraction returned no usable data.",
        )

    return ExtractionResult(
        screenshot_type=screenshot_type,
        data=parsed.model_dump(),
        raw_response=parsed.model_dump_json(),
    )


async def analyze_screenshot(image_url: str) -> ExtractionResult:
    """Full pipeline: classify then extract."""
    classification = await classify_screenshot(image_url)

    if (
        classification.screenshot_type == "unknown"
        or classification.confidence < MIN_SCREENSHOT_CLASSIFICATION_CONFIDENCE
    ):
        logger.info(
            "screenshot analysis skipped: low confidence type=%s confidence=%.2f",
            classification.screenshot_type,
            classification.confidence,
        )
        return ExtractionResult(
            screenshot_type="unknown",
            data={"classification": classification.__dict__},
            raw_response="Could not confidently classify this screenshot.",
        )

    logger.info(
        "screenshot analysis extracting type=%s confidence=%.2f",
        classification.screenshot_type,
        classification.confidence,
    )
    result = await extract_from_screenshot(image_url, classification.screenshot_type)
    result.data["classification"] = classification.__dict__
    return result


ModelT = TypeVar("ModelT", bound=BaseModel)


async def _call_vision(prompt: str, image_url: str, schema: type[ModelT]) -> ModelT | None:
    """Call the OpenAI vision model with an image and a strict response schema.

    Returns a validated `schema` instance, or `None` when the call cannot produce one
    (no API key, transport/API error, truncated response, or model refusal). Callers
    treat `None` as "unknown / no data" so a single screenshot never breaks the turn.
    """
    if not settings.openai_api_key:
        return None

    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_vision_timeout_seconds,
    )
    try:
        logger.debug("openai vision call start model=%s", settings.openai_vision_model)
        response = await client.responses.parse(
            model=settings.openai_vision_model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": image_url,
                            "detail": "high",
                        },
                    ],
                }
            ],
            text_format=schema,
            max_output_tokens=settings.openai_vision_max_output_tokens,
            reasoning=Reasoning(effort=settings.openai_vision_reasoning_effort),
        )
    except OpenAIError as error:
        status_code = getattr(error, "status_code", None)
        logger.warning(
            "screenshot vision request failed type=%s status=%s error=%s",
            schema.__name__,
            status_code,
            error,
        )
        return None

    logger.debug("openai vision call complete status=%s", response.status)

    if response.status == "incomplete":
        reason = response.incomplete_details.reason if response.incomplete_details else None
        logger.warning(
            "screenshot vision response incomplete type=%s reason=%s",
            schema.__name__,
            reason,
        )
        return None

    parsed = response.output_parsed
    if parsed is None:
        logger.warning(
            "screenshot vision response had no parsed output (possible refusal) type=%s text=%s",
            schema.__name__,
            response.output_text[:500],
        )
        return None

    return parsed
