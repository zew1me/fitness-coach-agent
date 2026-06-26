"""Screenshot classification and data extraction via vision model.

Two-step process:
1. Classify screenshot type (activity, wellness multi-day, wellness single-day, etc.)
2. Route to type-specific extraction prompt

Both steps use OpenAI Structured Outputs (strict `json_schema`) driven by the Pydantic
models in backend.models.screenshot, so the vision model is constrained to return JSON
matching our schema rather than free-form text we have to parse defensively.
"""

from __future__ import annotations

import logging
from typing import Any, TypeVar

from openai import AsyncOpenAI, OpenAIError
from openai.types.shared_params import Reasoning
from pydantic import BaseModel

from backend.config import settings
from backend.models.screenshot import (
    ActivityExtraction,
    ChartDateRange,
    ConfidenceEntry,
    ExtractionResult,
    GenericExtraction,
    GenericObservation,
    ScreenshotClassification,
    ScreenshotClassificationModel,
    ScreenshotType,
    TrainingLoadChartExtraction,
    TrainingLoadPoint,
    WellnessDayEntry,
    WellnessMultiExtraction,
    WellnessSingleExtraction,
)

# Re-export model symbols so callers that imported via this module continue to work.
__all__ = [
    "CLASSIFY_PROMPT",
    "EXTRACT_ACTIVITY_PROMPT",
    "EXTRACT_GENERIC_PROMPT",
    "EXTRACT_TRAINING_LOAD_CHART_PROMPT",
    "EXTRACT_WELLNESS_MULTI_PROMPT",
    "EXTRACT_WELLNESS_SINGLE_PROMPT",
    "MIN_SCREENSHOT_CLASSIFICATION_CONFIDENCE",
    "ActivityExtraction",
    "ChartDateRange",
    "ConfidenceEntry",
    "ExtractionResult",
    "GenericExtraction",
    "GenericObservation",
    "ScreenshotClassification",
    "ScreenshotClassificationModel",
    "ScreenshotType",
    "TrainingLoadChartExtraction",
    "TrainingLoadPoint",
    "WellnessDayEntry",
    "WellnessMultiExtraction",
    "WellnessSingleExtraction",
    "analyze_screenshot",
    "classify_screenshot",
    "extract_from_screenshot",
]

logger = logging.getLogger(__name__)

MIN_SCREENSHOT_CLASSIFICATION_CONFIDENCE = 0.3

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

EXTRACT_GENERIC_PROMPT = """Capture any athlete-relevant information visible in this
screenshot — a training plan, workout calendar, or anything else a coach might want to
know. Give a brief summary, then list the concrete data points you can read as
label/value observations. Use null/empty for anything not clearly visible. Do not guess."""


# `plan_or_calendar` has no specialized schema, and `unknown` / low-confidence
# classifications fall through to the same generic catch-all (see extract_from_screenshot),
# so the lead coach still receives whatever was legible instead of an empty result.
_GENERIC_EXTRACTION: tuple[str, type[BaseModel]] = (EXTRACT_GENERIC_PROMPT, GenericExtraction)

_EXTRACTION_BY_TYPE: dict[ScreenshotType, tuple[str, type[BaseModel]]] = {
    "activity_single": (EXTRACT_ACTIVITY_PROMPT, ActivityExtraction),
    "training_load_chart": (EXTRACT_TRAINING_LOAD_CHART_PROMPT, TrainingLoadChartExtraction),
    "wellness_multi_day": (EXTRACT_WELLNESS_MULTI_PROMPT, WellnessMultiExtraction),
    "wellness_single_day": (EXTRACT_WELLNESS_SINGLE_PROMPT, WellnessSingleExtraction),
    "plan_or_calendar": _GENERIC_EXTRACTION,
}


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
    screenshot_type: ScreenshotType,
) -> ExtractionResult:
    """Step 2: Extract structured data based on classification.

    Types without a specialized schema (`plan_or_calendar`, `unknown`) use the generic
    catch-all extractor so the lead coach still gets whatever was legible.
    """
    prompt, schema = _EXTRACTION_BY_TYPE.get(screenshot_type, _GENERIC_EXTRACTION)
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
    """Full pipeline: classify then extract.

    When the classifier is confident we use the type-specific extractor; otherwise
    (`unknown` or below the confidence floor) we still run the generic catch-all so the
    lead coach receives whatever was legible rather than nothing.
    """
    classification = await classify_screenshot(image_url)

    confident = (
        classification.screenshot_type != "unknown"
        and classification.confidence >= MIN_SCREENSHOT_CLASSIFICATION_CONFIDENCE
    )
    extract_type: ScreenshotType = classification.screenshot_type if confident else "unknown"

    logger.info(
        "screenshot analysis extracting type=%s confidence=%.2f confident=%s",
        extract_type,
        classification.confidence,
        confident,
    )
    result = await extract_from_screenshot(image_url, extract_type)
    result.data["classification"] = classification.__dict__
    return result


ModelT = TypeVar("ModelT", bound=BaseModel)


_HTTP_CLIENT_ERROR_MIN = 400
_HTTP_SERVER_ERROR_MIN = 500
# 4xx codes that are still transient (worth a retry/warning, not a loud error): request
# timeout and rate limit.
_TRANSIENT_CLIENT_ERRORS = frozenset({408, 429})


def _is_permanent_openai_error(status_code: int | None) -> bool:
    """4xx (other than 408/429) are client/config errors — a bad key, model, or schema —
    that will recur on every screenshot, so surface them loudly. Timeouts, rate limits,
    and 5xx are transient and only warrant a warning."""
    if not isinstance(status_code, int):
        return False
    is_client_error = _HTTP_CLIENT_ERROR_MIN <= status_code < _HTTP_SERVER_ERROR_MIN
    return is_client_error and status_code not in _TRANSIENT_CLIENT_ERRORS


def _refusal_text(response: Any) -> str | None:
    """Pull the refusal message out of the response's output parts.

    A model refusal lives in a `refusal`-type content part, not in `output_text`
    (which the SDK may leave as `None`), so we read it from the structured output.
    """
    for item in getattr(response, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "refusal":
                return getattr(part, "refusal", None)
    return None


def _parsed_or_none(response: Any, schema: type[ModelT]) -> ModelT | None:
    """Interpret a completed `responses.parse` call into a validated model or `None`."""
    if response.status in ("failed", "cancelled"):
        logger.error(
            "screenshot vision response %s type=%s error=%s",
            response.status,
            schema.__name__,
            getattr(response, "error", None),
        )
        return None

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
            "screenshot vision response had no parsed output (possible refusal) type=%s refusal=%s",
            schema.__name__,
            _refusal_text(response),
        )
        return None

    return parsed


async def _call_vision(prompt: str, image_url: str, schema: type[ModelT]) -> ModelT | None:
    """Call the OpenAI vision model with an image and a strict response schema.

    Returns a validated `schema` instance, or `None` when the call cannot produce one
    (no API key, transport/API error, a failed/cancelled/incomplete response, or no
    parsed output such as a refusal or content filter). Callers treat `None` as
    "unknown / no data" so a single screenshot never breaks the turn.
    """
    if not settings.openai_api_key:
        return None

    try:
        async with AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=settings.openai_vision_timeout_seconds,
        ) as client:
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
        log = logger.error if _is_permanent_openai_error(status_code) else logger.warning
        log(
            "screenshot vision request failed type=%s status=%s error=%s",
            schema.__name__,
            status_code,
            error,
        )
        return None

    logger.debug("openai vision call complete status=%s", response.status)
    return _parsed_or_none(response, schema)
