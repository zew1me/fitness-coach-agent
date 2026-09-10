"""Screenshot classification and data extraction via vision model.

Two-step process:
1. Classify screenshot type (activity, wellness multi-day, wellness single-day, etc.)
2. Route to type-specific extraction prompt

Both steps use OpenAI Structured Outputs (strict `json_schema`) driven by the Pydantic
models in backend.models.screenshot, so the vision model is constrained to return JSON
matching our schema rather than free-form text we have to parse defensively.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, TypeVar

import sentry_sdk
from openai import AsyncOpenAI, OpenAIError
from openai.types.shared_params import Reasoning
from pydantic import BaseModel
from sentry_sdk import metrics as sentry_metrics

from backend.config import settings
from backend.models.screenshot import (
    ActivityExtraction,
    ChartDateRange,
    ConfidenceEntry,
    ExtractionResult,
    GenericExtraction,
    GenericObservation,
    ScreenshotAnalysis,
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
    "ScreenshotAnalysis",
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
add a confidence entry naming the field and your confidence from 0.0 to 1.0.
Place any visible values that do not fit the fields above into additional_observations
as label/value pairs."""

EXTRACT_WELLNESS_MULTI_PROMPT = """Extract the daily wellness/recovery data shown in this
screenshot. It may cover multiple days — return one entry per visible day.
Use null for anything not clearly visible. Do not guess values.
For each day entry, place any visible values that do not fit the structured fields
into that entry's additional_observations as label/value pairs.
Place any header-level or screenshot-wide values (e.g. date range labels, summary
stats, or app-level metadata) that do not belong to a specific day into the
top-level additional_observations as label/value pairs."""

EXTRACT_WELLNESS_SINGLE_PROMPT = """Extract the day's wellness/recovery data shown in this
screenshot. Use null for anything not clearly visible. Do not guess values.
Place any visible values that do not fit the fields above into additional_observations
as label/value pairs."""

EXTRACT_TRAINING_LOAD_CHART_PROMPT = """Extract data from this training load chart.
It may show CTL/fitness, ATL/fatigue, TSB/form, training stress, or similar time-series
lines. Capture the visible date range, axis labels, and readable value points (one series
entry per readable point). Use null when dates or labels are not visible. Approximate a
value only when the axis/grid makes it clear, and lower the confidence for approximate
points. Do not guess hidden values.
Place any visible values that do not fit the fields above into additional_observations
as label/value pairs."""

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


ANALYZE_PROMPT = f"""Analyze this screenshot in one pass: classify it, then extract its data.

Step 1 — set `screenshot_type` to exactly one of:

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

Also set `source_app_hint`, `date_range_hint`, and a `confidence` from 0.0 to 1.0.

Step 2 — fill in exactly ONE payload field, the one matching the type you chose, and
leave every other payload field null:

- activity_single -> `activity`: {EXTRACT_ACTIVITY_PROMPT}

- wellness_multi_day -> `wellness_multi`: {EXTRACT_WELLNESS_MULTI_PROMPT}

- wellness_single_day -> `wellness_single`: {EXTRACT_WELLNESS_SINGLE_PROMPT}

- training_load_chart -> `training_load_chart`: {EXTRACT_TRAINING_LOAD_CHART_PROMPT}

- plan_or_calendar or unknown -> `generic`: {EXTRACT_GENERIC_PROMPT}

If your confidence in the type is below {MIN_SCREENSHOT_CLASSIFICATION_CONFIDENCE}, fill
`generic` instead of a typed field, so nothing legible is lost."""


# Which payload field on ScreenshotAnalysis carries each type's extraction.
_ANALYSIS_FIELD_BY_TYPE: dict[ScreenshotType, str] = {
    "activity_single": "activity",
    "wellness_multi_day": "wellness_multi",
    "wellness_single_day": "wellness_single",
    "training_load_chart": "training_load_chart",
    "plan_or_calendar": "generic",
}


def _unknown_classification() -> ScreenshotClassification:
    """The classification reported when the vision model gave us nothing to go on."""
    return ScreenshotClassification(
        screenshot_type="unknown",
        source_app_hint=None,
        date_range_hint=None,
        confidence=0.0,
    )


async def classify_screenshot(image_url: str) -> ScreenshotClassification:
    """Step 1: Classify a screenshot into a category."""
    parsed = await _call_vision(CLASSIFY_PROMPT, image_url, ScreenshotClassificationModel)
    if parsed is None:
        return _unknown_classification()

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


def _select_payload(
    parsed: ScreenshotAnalysis,
    extract_type: ScreenshotType,
    *,
    confident: bool,
) -> tuple[ScreenshotType, BaseModel | None]:
    """Pick which populated payload branch to return, and the type that describes it.

    Preference order: the branch matching the classification, then the generic
    catch-all, then any other populated branch. The last step matters because the model
    can classify weakly while still filling a typed branch correctly — reporting nothing
    there would throw away data we already paid for. When it fires, the returned type is
    corrected to match the branch, so `screenshot_type` always describes `data`'s shape.
    """
    preferred = _ANALYSIS_FIELD_BY_TYPE.get(extract_type, "generic") if confident else "generic"

    payload: BaseModel | None = getattr(parsed, preferred, None)
    if payload is not None:
        return extract_type, payload

    if parsed.generic is not None:
        return extract_type, parsed.generic

    for candidate_type, field in _ANALYSIS_FIELD_BY_TYPE.items():
        payload = getattr(parsed, field, None)
        if payload is not None:
            return candidate_type, payload

    return extract_type, None


async def analyze_screenshot(image_url: str) -> ExtractionResult:
    """Classify and extract a screenshot in a single vision call.

    One call does both jobs. The previous pipeline classified in one call purely to
    choose the second call's schema, which meant a full extra model round-trip — and
    OpenAI fetching the image a second time — on every request, including the case where
    the classification was too weak to use.

    Set `screenshot_legacy_two_call_analysis` to fall back to that pipeline.
    """
    if settings.screenshot_legacy_two_call_analysis:
        return await _analyze_screenshot_two_call(image_url)

    parsed = await _call_vision(ANALYZE_PROMPT, image_url, ScreenshotAnalysis)
    if parsed is None:
        return ExtractionResult(
            screenshot_type="unknown",
            data={"classification": _unknown_classification().__dict__},
            raw_response="Vision extraction returned no usable data.",
        )

    classification = ScreenshotClassification(
        screenshot_type=parsed.screenshot_type,
        source_app_hint=parsed.source_app_hint,
        date_range_hint=parsed.date_range_hint,
        confidence=parsed.confidence,
    )
    confident = (
        parsed.screenshot_type != "unknown"
        and parsed.confidence >= MIN_SCREENSHOT_CLASSIFICATION_CONFIDENCE
    )
    extract_type: ScreenshotType = parsed.screenshot_type if confident else "unknown"

    extract_type, payload = _select_payload(parsed, extract_type, confident=confident)

    logger.info(
        "screenshot analysis extracted type=%s confidence=%.2f confident=%s payload=%s",
        extract_type,
        parsed.confidence,
        confident,
        type(payload).__name__ if payload is not None else None,
    )

    if payload is None:
        return ExtractionResult(
            screenshot_type=extract_type,
            data={"classification": classification.__dict__},
            raw_response="Vision extraction returned no usable data.",
        )

    data = payload.model_dump()
    data["classification"] = classification.__dict__
    return ExtractionResult(
        screenshot_type=extract_type,
        data=data,
        raw_response=payload.model_dump_json(),
    )


async def _analyze_screenshot_two_call(image_url: str) -> ExtractionResult:
    """Legacy pipeline: classify in one vision call, then extract in a second.

    Retained only as a rollback for `screenshot_legacy_two_call_analysis`; delete it
    once single-call extraction quality is confirmed in production.
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


def _is_reasoning_model_mismatch(error: OpenAIError) -> bool:
    """Return True when OpenAI rejected the request because the configured model does not
    support the `reasoning` parameter.

    The startup validator in backend/config.py should prevent this, but a model swap via
    env var without a restart (or a future model that wasn't added to the approved list)
    could still reach here. We detect it explicitly so Sentry captures it with operator-
    actionable context rather than surfacing as a generic 400.
    """
    if getattr(error, "status_code", None) != 400:  # noqa: PLR2004
        return False
    msg = str(error).lower()
    return "reasoning" in msg and any(
        kw in msg for kw in ("unsupported", "not supported", "invalid")
    )


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


# Single-slot cache. A dict rather than a module-level name so the accessors mutate it
# in place instead of rebinding a global.
_vision_client_cache: dict[str, AsyncOpenAI] = {}


def _get_vision_client() -> AsyncOpenAI:
    """Return the process-wide vision client, creating it on first use.

    The client owns an HTTP connection pool, so building one per call cost a fresh TLS
    handshake to api.openai.com on every vision request. It is deliberately never closed:
    on Vercel the process is frozen between requests and reclaimed wholesale, so a
    long-lived pool is what lets a warm instance skip the handshake. Mirrors the boto3
    memoization in backend/services/r2.py.
    """
    client = _vision_client_cache.get("client")
    if client is None:
        client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            max_retries=settings.openai_max_retries,
            timeout=settings.openai_vision_timeout_seconds,
        )
        _vision_client_cache["client"] = client
    return client


def reset_vision_client() -> None:
    """Drop the memoized client so a later call rebuilds it. For tests."""
    _vision_client_cache.clear()


_VISION_STAGE_BY_SCHEMA: dict[type[BaseModel], str] = {
    ScreenshotClassificationModel: "classify",
    ScreenshotAnalysis: "analyze",
}


def _vision_stage(schema: type[BaseModel]) -> str:
    """Telemetry label for a vision call: the combined pass, or a legacy half of it."""
    return _VISION_STAGE_BY_SCHEMA.get(schema, "extract")


# Token counts worth aggregating, as (usage attribute, details attribute, metric name).
# `details` names a nested field on ResponseUsage; None means read it off usage directly.
_VISION_TOKEN_METRICS: tuple[tuple[str, str | None, str], ...] = (
    ("input_tokens", None, "input_tokens"),
    ("output_tokens", None, "output_tokens"),
    ("total_tokens", None, "total_tokens"),
    ("cached_tokens", "input_tokens_details", "cached_tokens"),
    # Reasoning tokens are emitted serially, so this is the closest thing to a direct
    # explanation of a slow extraction call.
    ("reasoning_tokens", "output_tokens_details", "reasoning_tokens"),
)


def _vision_usage(response: Any) -> dict[str, int]:
    """Pull the token counts off an OpenAI response, skipping anything absent."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}

    counts: dict[str, int] = {}
    for attribute, details, metric_name in _VISION_TOKEN_METRICS:
        source = usage if details is None else getattr(usage, details, None)
        value = getattr(source, attribute, None)
        if value is not None:
            counts[metric_name] = value
    return counts


def _record_vision_call(
    span: Any,
    *,
    schema: type[BaseModel],
    started: float,
    outcome: str,
    response: Any = None,
) -> None:
    """Report one vision call as span attributes and as trace metrics.

    Traces already carry a `POST /v1/responses` httpx span, but both calls of the old
    two-call pipeline shared one description, so the classify/extract split was
    invisible in aggregate. `stage` is what separates them.

    Durations and token counts go out as distributions rather than as fields on a log
    line, so Sentry aggregates them natively (p50/p95 by stage) instead of requiring a
    text search to be parsed back into numbers. The span attributes carry the same
    values for per-request drill-down, and use `gen_ai.*` naming where a convention
    exists so Sentry's AI views pick them up.
    """
    duration_ms = (time.perf_counter() - started) * 1000
    stage = _vision_stage(schema)
    counts = _vision_usage(response)

    # Grouping keys, attached to every metric so each can be split by stage or outcome.
    attributes = {
        "stage": stage,
        "schema": schema.__name__,
        "outcome": outcome,
        "model": settings.openai_vision_model,
    }

    span.update_data(
        {
            "gen_ai.operation.name": f"screenshot.{stage}",
            "gen_ai.request.model": settings.openai_vision_model,
            **{f"gen_ai.usage.{name}": value for name, value in counts.items()},
            "screenshot.stage": stage,
            "screenshot.schema": schema.__name__,
            "screenshot.outcome": outcome,
            "screenshot.duration_ms": round(duration_ms, 1),
        }
    )

    sentry_metrics.distribution(
        "screenshot.vision.duration",
        duration_ms,
        unit="millisecond",
        attributes=attributes,
    )
    for name, value in counts.items():
        sentry_metrics.distribution(
            f"screenshot.vision.{name}",
            value,
            unit="none",
            attributes=attributes,
        )


async def _call_vision(prompt: str, image_url: str, schema: type[ModelT]) -> ModelT | None:
    """Call the OpenAI vision model with an image and a strict response schema.

    Returns a validated `schema` instance, or `None` when the call cannot produce one
    (no API key, transport/API error, a timeout, a failed/cancelled/incomplete
    response, or no parsed output such as a refusal or content filter). Callers treat
    `None` as "unknown / no data" so a single screenshot never breaks the turn.

    `openai_vision_timeout_seconds` bounds a single attempt, but the SDK retries
    internally, so the enclosing `openai_vision_total_timeout_seconds` guard is what
    stops a stalled vision call from consuming the whole serverless request budget.

    Every exit path reports through `_record_vision_call`, so a timeout or an error is
    as visible in the latency data as a success.
    """
    if not settings.openai_api_key:
        return None

    started = time.perf_counter()
    with sentry_sdk.start_span(op="gen_ai.vision", name=f"vision {_vision_stage(schema)}") as span:
        try:
            async with asyncio.timeout(settings.openai_vision_total_timeout_seconds):
                logger.debug("openai vision call start model=%s", settings.openai_vision_model)
                response = await _get_vision_client().responses.parse(
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
        except TimeoutError:
            _record_vision_call(span, schema=schema, started=started, outcome="timeout")
            logger.warning(
                "screenshot vision call exceeded its total budget type=%s budget=%ss",
                schema.__name__,
                settings.openai_vision_total_timeout_seconds,
            )
            return None
        except OpenAIError as error:
            status_code = getattr(error, "status_code", None)
            _record_vision_call(span, schema=schema, started=started, outcome="error")
            if _is_reasoning_model_mismatch(error):
                # The model rejected the `reasoning` parameter — config mismatch that slipped
                # past the startup validator. Capture explicitly so Sentry shows the operator
                # which model is misconfigured, not just a raw 400.
                with sentry_sdk.new_scope() as scope:
                    scope.set_tag("error.category", "reasoning_model_mismatch")
                    scope.set_extra("vision_model", settings.openai_vision_model)
                    scope.set_extra(
                        "operator_hint",
                        f"Model {settings.openai_vision_model!r} rejected the reasoning "
                        f"parameter. Set OPENAI_VISION_MODEL to a reasoning-capable model "
                        f"(o1*, o3*, o4*, gpt-5*), or add its prefix to "
                        f"_REASONING_CAPABLE_MODEL_PREFIXES in backend/config.py.",
                    )
                    sentry_sdk.capture_exception(error)
                logger.exception(
                    "screenshot vision model config error: %r does not support reasoning — "
                    "set OPENAI_VISION_MODEL to a reasoning-capable model "
                    "(o1*, o3*, o4*, gpt-5*); type=%s status=%s",
                    settings.openai_vision_model,
                    schema.__name__,
                    status_code,
                )
            else:
                log = logger.error if _is_permanent_openai_error(status_code) else logger.warning
                log(
                    "screenshot vision request failed type=%s status=%s error=%s",
                    schema.__name__,
                    status_code,
                    error,
                )
            return None

        logger.debug("openai vision call complete status=%s", response.status)
        parsed = _parsed_or_none(response, schema)
        _record_vision_call(
            span,
            schema=schema,
            started=started,
            outcome="ok" if parsed is not None else "unparsed",
            response=response,
        )
        return parsed
