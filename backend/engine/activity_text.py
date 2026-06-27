"""Extract and merge compact activity summaries from chat text."""

from __future__ import annotations

import copy
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
from pydantic import BaseModel, Field

from backend.config import settings
from backend.engine.tss import compute_tss
from backend.models.athlete import AthleteProfile, SportThreshold
from backend.models.training import Activity

logger = logging.getLogger(__name__)

SUMMARY_SCHEMA_VERSION = 1
SUMMARY_SCHEMA_NAME = "activity_summary_v1"
RESPONSES_API_URL = "https://api.openai.com/v1/responses"

Extractor = Callable[[str], Awaitable["ActivityTextExtraction"]]


class ActivityTextExtractionUnavailableError(RuntimeError):
    """Raised when OpenAI-backed activity text extraction cannot run."""


ActivityTextExtractionUnavailable = ActivityTextExtractionUnavailableError


class AdditionalImportantData(BaseModel):
    key: str
    value: str
    confidence: float = Field(ge=0, le=1)


class ExtractedFoodItem(BaseModel):
    name: str
    quantity: float | None = None
    serving_hint: str | None = None
    brand_hint: str | None = None
    timing_hint: str | None = None
    confidence: float = Field(ge=0, le=1)


class NutritionEstimate(BaseModel):
    item_name: str
    carbs_g: float | None = None
    carbs_g_confidence: float | None = Field(default=None, ge=0, le=1)
    calories_kcal: float | None = None
    calories_kcal_confidence: float | None = Field(default=None, ge=0, le=1)
    source_title: str | None = None
    source_url: str | None = None


class ActivityTextExtraction(BaseModel):
    sport: str | None = None
    sport_confidence: float | None = Field(default=None, ge=0, le=1)
    sub_sport: str | None = None
    sub_sport_confidence: float | None = Field(default=None, ge=0, le=1)
    activity_date: str | None = None
    activity_date_confidence: float | None = Field(default=None, ge=0, le=1)
    elapsed_duration_seconds: int | None = None
    elapsed_duration_seconds_confidence: float | None = Field(default=None, ge=0, le=1)
    moving_duration_seconds: int | None = None
    moving_duration_seconds_confidence: float | None = Field(default=None, ge=0, le=1)
    avg_hr_bpm: int | None = None
    avg_hr_bpm_confidence: float | None = Field(default=None, ge=0, le=1)
    max_hr_bpm: int | None = None
    max_hr_bpm_confidence: float | None = Field(default=None, ge=0, le=1)
    avg_power_watts: int | None = None
    avg_power_watts_confidence: float | None = Field(default=None, ge=0, le=1)
    normalized_power_watts: int | None = None
    normalized_power_watts_confidence: float | None = Field(default=None, ge=0, le=1)
    best_power_watts: int | None = None
    best_power_watts_confidence: float | None = Field(default=None, ge=0, le=1)
    best_power_window_seconds: int | None = None
    best_power_window_seconds_confidence: float | None = Field(default=None, ge=0, le=1)
    athlete_notes: str | None = None
    athlete_notes_confidence: float | None = Field(default=None, ge=0, le=1)
    rpe: int | None = Field(default=None, ge=1, le=10)
    rpe_confidence: float | None = Field(default=None, ge=0, le=1)
    gut_comfort_1_10: int | None = Field(default=None, ge=1, le=10)
    gut_comfort_1_10_confidence: float | None = Field(default=None, ge=0, le=1)
    overdid_it_flag: bool | None = None
    overdid_it_flag_confidence: float | None = Field(default=None, ge=0, le=1)
    food_items: list[ExtractedFoodItem] = Field(default_factory=list)
    nutrition_estimates: list[NutritionEstimate] = Field(default_factory=list)
    additional_important_data: list[AdditionalImportantData] = Field(default_factory=list)


@dataclass
class ActivityTextBuildResult:
    activity: Activity | None
    missing: list[str]
    raw_extraction: dict[str, Any]


async def extract_activity_text(text: str) -> ActivityTextExtraction:
    """Use OpenAI structured outputs and web search to extract activity text."""
    if not settings.openai_api_key:
        raise ActivityTextExtractionUnavailable("OpenAI activity text extraction unavailable.")

    schema = ActivityTextExtraction.model_json_schema()
    try:
        async with httpx.AsyncClient(
            timeout=settings.openai_activity_text_timeout_seconds
        ) as client:
            response = await client.post(
                RESPONSES_API_URL,
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.openai_activity_text_model,
                    "input": [
                        {
                            "role": "system",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": _activity_text_extraction_instructions(),
                                }
                            ],
                        },
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": text}],
                        },
                    ],
                    "tools": [{"type": "web_search"}],
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": "activity_text_extraction",
                            "strict": False,
                            "schema": schema,
                        }
                    },
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("OpenAI activity text extraction failed", exc_info=True)
        raise ActivityTextExtractionUnavailable(
            "OpenAI activity text extraction unavailable."
        ) from exc

    output_text = _extract_response_output_text(payload)
    if output_text is None:
        logger.warning(
            "OpenAI response contained no extractable output_text; output=%r",
            payload.get("output"),
        )
        raise ActivityTextExtractionUnavailable("OpenAI activity text extraction unavailable.")

    try:
        return ActivityTextExtraction.model_validate_json(output_text)
    except ValueError as exc:
        logger.warning("OpenAI activity text extraction returned invalid JSON", exc_info=True)
        raise ActivityTextExtractionUnavailable(
            "OpenAI activity text extraction unavailable."
        ) from exc


def _activity_text_extraction_instructions() -> str:
    return (
        "Extract an endurance activity summary from the user's text. Use web_search to enrich "
        "food and drink items with nutrition data when the text mentions nutrition. Return only "
        "the requested JSON schema. Use null for fields that are absent. Include confidence "
        "scores for every extracted or estimated field. For food, extract food_items first, then "
        "estimate carbs_g and calories_kcal with source_title/source_url where web evidence is "
        "available. Generic food like '2 gels' should produce lower-confidence nutrition "
        "estimates, not a gels_count field. Put other meaningful observations into "
        "additional_important_data as key/value/confidence entries."
    )


def _extract_response_output_text(payload: dict[str, Any]) -> str | None:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return content.get("text")
    return None


def _threshold_for_sport(
    thresholds: list[SportThreshold], sport: str | None
) -> SportThreshold | None:
    return next((threshold for threshold in thresholds if threshold.sport == sport), None)


def _set_if_not_none(target: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


def _thresholds_used(
    profile: AthleteProfile,
    threshold: SportThreshold | None,
) -> dict[str, int | float]:
    values: dict[str, int | float] = {}
    if threshold is not None:
        _set_if_not_none(values, "lt1_power_w", threshold.lt1_power_watts)
        _set_if_not_none(values, "lt1_hr_bpm", threshold.lt1_hr_bpm)
        _set_if_not_none(values, "lt1_pace_s_per_km", threshold.lt1_pace_sec_per_km)
        _set_if_not_none(values, "lt2_pace_s_per_km", threshold.lt2_pace_sec_per_km)
        _set_if_not_none(values, "ftp_w", threshold.lt2_power_watts)
        _set_if_not_none(values, "lt2_power_w", threshold.lt2_power_watts)
        _set_if_not_none(values, "lt2_hr_bpm", threshold.lt2_hr_bpm)
        _set_if_not_none(values, "lthr_bpm", threshold.lt2_hr_bpm)
    _set_if_not_none(values, "hr_max_bpm", profile.max_hr_bpm)
    _set_if_not_none(values, "hr_resting_bpm", profile.resting_hr_bpm)
    _set_if_not_none(values, "body_mass_kg", profile.weight_kg)
    return values


def _empty_summary(source: str) -> dict[str, Any]:
    return {
        "schema": SUMMARY_SCHEMA_NAME,
        "session": {},
        "thresholds_used": {},
        "heart_rate": {},
        "power": {},
        "pace": {},
        "cadence": {},
        "load": {},
        "durability": {},
        "terrain": {},
        "environment": {},
        "fueling": {},
        "readiness": {},
        "subjective": {},
        "food_items": [],
        "additional_important_data": [],
        "estimates": {},
        "data_quality": {
            "source": source,
            "has_power": False,
            "has_hr": False,
            "has_gps": False,
            "has_rr_intervals": False,
            "estimated_from_text": source == "text_extract",
        },
    }


def _add_session_summary(summary: dict[str, Any], extraction: ActivityTextExtraction) -> None:
    session = summary["session"]
    estimates = summary["estimates"]
    if extraction.sport is not None:
        session["sport"] = extraction.sport
        estimates["estimated_sport"] = extraction.sport
        estimates["estimated_sport_confidence"] = extraction.sport_confidence
    if extraction.sub_sport is not None:
        session["sub_sport"] = extraction.sub_sport
        estimates["estimated_sub_sport_confidence"] = extraction.sub_sport_confidence
    if extraction.activity_date is not None:
        session["date_start"] = extraction.activity_date
        estimates["estimated_activity_date_confidence"] = extraction.activity_date_confidence
    if extraction.elapsed_duration_seconds is not None:
        session["duration_elapsed_s"] = extraction.elapsed_duration_seconds
        estimates["estimated_duration_elapsed_s_confidence"] = (
            extraction.elapsed_duration_seconds_confidence
        )
    if extraction.moving_duration_seconds is not None:
        session["duration_moving_s"] = extraction.moving_duration_seconds
        estimates["estimated_duration_moving_s"] = extraction.moving_duration_seconds
        estimates["estimated_duration_moving_s_confidence"] = (
            extraction.moving_duration_seconds_confidence
        )


def _add_stream_summary(summary: dict[str, Any], extraction: ActivityTextExtraction) -> None:
    estimates = summary["estimates"]
    if extraction.avg_hr_bpm is not None:
        summary["heart_rate"]["avg_bpm"] = extraction.avg_hr_bpm
        estimates["estimated_avg_hr_bpm_confidence"] = extraction.avg_hr_bpm_confidence
        summary["data_quality"]["has_hr"] = True
    if extraction.max_hr_bpm is not None:
        summary["heart_rate"]["max_bpm"] = extraction.max_hr_bpm
        estimates["estimated_max_hr_bpm_confidence"] = extraction.max_hr_bpm_confidence
        summary["data_quality"]["has_hr"] = True

    if extraction.avg_power_watts is not None:
        summary["power"]["avg_w"] = extraction.avg_power_watts
        estimates["estimated_avg_power_watts_confidence"] = extraction.avg_power_watts_confidence
        summary["data_quality"]["has_power"] = True
    if extraction.normalized_power_watts is not None:
        summary["power"]["normalized_w"] = extraction.normalized_power_watts
        estimates["estimated_normalized_power_watts_confidence"] = (
            extraction.normalized_power_watts_confidence
        )
        summary["data_quality"]["has_power"] = True
    if extraction.best_power_watts is not None and extraction.best_power_window_seconds is not None:
        summary["power"]["bests_w"] = {
            f"{extraction.best_power_window_seconds}s": extraction.best_power_watts
        }
        estimates["estimated_power_best_confidence"] = extraction.best_power_watts_confidence


def _add_context_summary(summary: dict[str, Any], extraction: ActivityTextExtraction) -> None:
    summary["food_items"] = [
        item.model_dump(mode="json", exclude_none=True) for item in extraction.food_items
    ]
    summary["additional_important_data"] = [
        item.model_dump(mode="json", exclude_none=True)
        for item in extraction.additional_important_data
    ]
    _add_nutrition_summary(summary, extraction)

    if extraction.gut_comfort_1_10 is not None:
        summary["fueling"]["gut_comfort_1_10"] = extraction.gut_comfort_1_10
        summary["estimates"]["estimated_gut_comfort_1_10_confidence"] = (
            extraction.gut_comfort_1_10_confidence
        )
    if extraction.rpe is not None:
        summary["subjective"]["rpe_1_10"] = extraction.rpe
        summary["estimates"]["estimated_rpe_confidence"] = extraction.rpe_confidence
    if extraction.overdid_it_flag is not None:
        summary["subjective"]["overdid_it_flag"] = extraction.overdid_it_flag
        summary["estimates"]["estimated_overdid_it_flag_confidence"] = (
            extraction.overdid_it_flag_confidence
        )
    if extraction.athlete_notes is not None:
        summary["subjective"]["athlete_notes"] = extraction.athlete_notes
        summary["estimates"]["estimated_athlete_notes_confidence"] = (
            extraction.athlete_notes_confidence
        )


def _add_nutrition_summary(summary: dict[str, Any], extraction: ActivityTextExtraction) -> None:
    carbs = sum(item.carbs_g or 0 for item in extraction.nutrition_estimates)
    calories = sum(item.calories_kcal or 0 for item in extraction.nutrition_estimates)
    if carbs > 0:
        summary["fueling"]["carbs_g"] = round(carbs, 1)
        summary["fueling"]["carbs_g_confidence"] = _weighted_confidence(
            [(item.carbs_g, item.carbs_g_confidence) for item in extraction.nutrition_estimates]
        )
    if calories > 0:
        summary["fueling"]["calories_kcal"] = round(calories, 1)
        summary["fueling"]["calories_kcal_confidence"] = _weighted_confidence(
            [
                (item.calories_kcal, item.calories_kcal_confidence)
                for item in extraction.nutrition_estimates
            ]
        )
    if extraction.nutrition_estimates:
        summary["fueling"]["nutrition_estimates"] = [
            item.model_dump(mode="json", exclude_none=True)
            for item in extraction.nutrition_estimates
        ]


def _merge_nutrition_summary(summary: dict[str, Any], extraction: ActivityTextExtraction) -> None:
    """Like _add_nutrition_summary but accumulates totals (update path)."""
    carbs = sum(item.carbs_g or 0 for item in extraction.nutrition_estimates)
    calories = sum(item.calories_kcal or 0 for item in extraction.nutrition_estimates)
    if carbs > 0:
        existing_carbs = summary.get("fueling", {}).get("carbs_g") or 0
        summary["fueling"]["carbs_g"] = round(existing_carbs + carbs, 1)
        summary["fueling"]["carbs_g_confidence"] = _weighted_confidence(
            [(item.carbs_g, item.carbs_g_confidence) for item in extraction.nutrition_estimates]
        )
    if calories > 0:
        existing_calories = summary.get("fueling", {}).get("calories_kcal") or 0
        summary["fueling"]["calories_kcal"] = round(existing_calories + calories, 1)
        summary["fueling"]["calories_kcal_confidence"] = _weighted_confidence(
            [
                (item.calories_kcal, item.calories_kcal_confidence)
                for item in extraction.nutrition_estimates
            ]
        )
    if extraction.nutrition_estimates:
        existing_estimates = summary.get("fueling", {}).get("nutrition_estimates") or []
        summary["fueling"]["nutrition_estimates"] = existing_estimates + [
            item.model_dump(mode="json", exclude_none=True)
            for item in extraction.nutrition_estimates
        ]


def _merge_context_summary(summary: dict[str, Any], extraction: ActivityTextExtraction) -> None:
    """Like _add_context_summary but extends lists instead of replacing them (update path)."""
    if extraction.food_items:
        existing = summary.get("food_items") or []
        summary["food_items"] = existing + [
            item.model_dump(mode="json", exclude_none=True) for item in extraction.food_items
        ]
    if extraction.additional_important_data:
        existing = summary.get("additional_important_data") or []
        summary["additional_important_data"] = existing + [
            item.model_dump(mode="json", exclude_none=True)
            for item in extraction.additional_important_data
        ]
    _merge_nutrition_summary(summary, extraction)
    if extraction.gut_comfort_1_10 is not None:
        summary["fueling"]["gut_comfort_1_10"] = extraction.gut_comfort_1_10
        summary["estimates"]["estimated_gut_comfort_1_10_confidence"] = (
            extraction.gut_comfort_1_10_confidence
        )
    if extraction.overdid_it_flag is not None:
        summary["subjective"]["overdid_it_flag"] = extraction.overdid_it_flag
        summary["estimates"]["estimated_overdid_it_flag_confidence"] = (
            extraction.overdid_it_flag_confidence
        )
    if extraction.athlete_notes is not None:
        summary["subjective"]["athlete_notes"] = extraction.athlete_notes
        summary["estimates"]["estimated_athlete_notes_confidence"] = (
            extraction.athlete_notes_confidence
        )


def _weighted_confidence(values: list[tuple[float | None, float | None]]) -> float | None:
    weighted_values = [
        (value, confidence)
        for value, confidence in values
        if value is not None and confidence is not None
    ]
    total = sum(value for value, _confidence in weighted_values)
    if total <= 0:
        return None
    return round(sum(value * confidence for value, confidence in weighted_values) / total, 2)


def _try_parse_iso_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        logger.warning("activity_date could not be parsed as ISO date: %r", value)
        return None


def _build_summary(
    extraction: ActivityTextExtraction,
    *,
    source: str,
    profile: AthleteProfile,
    threshold: SportThreshold | None,
) -> dict[str, Any]:
    summary = _empty_summary(source)
    _add_session_summary(summary, extraction)
    _add_stream_summary(summary, extraction)
    _add_context_summary(summary, extraction)
    summary["thresholds_used"] = _thresholds_used(profile, threshold)
    return summary


def _add_load_metrics(activity: Activity, summary: dict[str, Any], profile: AthleteProfile) -> None:
    duration_seconds = activity.duration_seconds
    if duration_seconds is None or duration_seconds <= 0:
        return

    thresholds = summary["thresholds_used"]
    ftp = thresholds.get("ftp_w")
    if activity.normalized_power_watts is not None and isinstance(ftp, int | float) and ftp > 0:
        intensity_factor = round(activity.normalized_power_watts / ftp, 2)
        tss = round(
            compute_tss(
                duration_seconds,
                sport=activity.sport,
                normalized_power=activity.normalized_power_watts,
                ftp=int(ftp),
            ),
            1,
        )
        activity.intensity_factor = intensity_factor
        activity.tss = tss
        summary["power"]["intensity_factor"] = intensity_factor
        summary["load"]["primary_load"] = tss
        summary["load"]["tss_power"] = tss
        summary["estimates"]["estimated_tss_power"] = tss
        summary["estimates"]["estimated_tss_power_confidence"] = 0.82

    if activity.avg_power_watts is not None:
        work_kj = round(activity.avg_power_watts * duration_seconds / 1000, 1)
        summary["load"]["work_kj"] = work_kj
        summary["power"]["work_kj"] = work_kj

    if activity.rpe is not None:
        summary["load"]["session_rpe_load"] = round((duration_seconds / 60) * activity.rpe, 1)

    if (
        activity.tss is None
        and activity.avg_hr_bpm
        and profile.resting_hr_bpm
        and profile.max_hr_bpm
    ):
        tss_hr = round(
            compute_tss(
                duration_seconds,
                sport=activity.sport,
                avg_hr=activity.avg_hr_bpm,
                resting_hr=profile.resting_hr_bpm,
                max_hr=profile.max_hr_bpm,
                biological_sex=profile.biological_sex or "not_specified",
            ),
            1,
        )
        activity.tss = tss_hr
        summary["load"]["primary_load"] = tss_hr
        summary["load"]["tss_hr"] = tss_hr


async def build_activity_from_text(
    text: str,
    *,
    user_id: str,
    profile: AthleteProfile,
    thresholds: list[SportThreshold],
    extractor: Extractor | None = None,
) -> ActivityTextBuildResult:
    extraction = await (extractor or extract_activity_text)(text)
    useful_activity_signal = (
        extraction.moving_duration_seconds
        or extraction.elapsed_duration_seconds
        or extraction.avg_hr_bpm
        or extraction.avg_power_watts
        or extraction.rpe
    )
    activity_date = _try_parse_iso_date(extraction.activity_date)
    missing = [
        name
        for name, value in (
            ("sport", extraction.sport),
            ("activity_date", activity_date),
            ("duration or metric", useful_activity_signal),
        )
        if value is None
    ]
    raw_extraction = {
        "input_text": text,
        "openai_extraction": extraction.model_dump(mode="json", exclude_none=True),
    }
    if missing:
        return ActivityTextBuildResult(
            activity=None,
            missing=missing,
            raw_extraction=raw_extraction,
        )
    assert activity_date is not None

    threshold = _threshold_for_sport(thresholds, extraction.sport)
    summary = _build_summary(
        extraction,
        source="text_extract",
        profile=profile,
        threshold=threshold,
    )
    duration_seconds = extraction.moving_duration_seconds or extraction.elapsed_duration_seconds
    activity = Activity(
        user_id=user_id,
        sport=extraction.sport or "general",
        activity_date=activity_date,
        duration_seconds=duration_seconds,
        avg_hr_bpm=extraction.avg_hr_bpm,
        max_hr_bpm=extraction.max_hr_bpm,
        avg_power_watts=extraction.avg_power_watts,
        normalized_power_watts=extraction.normalized_power_watts,
        rpe=extraction.rpe,
        athlete_notes=extraction.athlete_notes,
        fueling_notes=_fueling_notes(extraction),
        source="text_extract",
        raw_extraction=raw_extraction,
        summary_schema_version=SUMMARY_SCHEMA_VERSION,
        activity_summary=summary,
    )
    _add_load_metrics(activity, summary, profile)
    return ActivityTextBuildResult(activity=activity, missing=[], raw_extraction=raw_extraction)


def _parse_iso_date(value: str | None) -> date:
    if value is None:
        raise ValueError("Activity date is required.")
    parsed = _try_parse_iso_date(value)
    if parsed is None:
        raise ValueError("Activity date must be an ISO date.")
    return parsed


def _activity_summary_for_update(existing: Activity) -> dict[str, Any]:
    summary = (
        copy.deepcopy(existing.activity_summary)
        if existing.activity_summary
        else _empty_summary(existing.source)
    )
    summary.setdefault("schema", SUMMARY_SCHEMA_NAME)
    for key in (
        "session",
        "thresholds_used",
        "heart_rate",
        "power",
        "pace",
        "cadence",
        "load",
        "durability",
        "terrain",
        "environment",
        "fueling",
        "readiness",
        "subjective",
        "food_items",
        "additional_important_data",
        "estimates",
        "data_quality",
    ):
        summary.setdefault(key, [] if key in {"food_items", "additional_important_data"} else {})
    summary["data_quality"].setdefault("source", existing.source)
    return summary


def _apply_text_update_session_fields(
    updated: Activity,
    summary: dict[str, Any],
    extraction: ActivityTextExtraction,
) -> None:
    if extraction.moving_duration_seconds is not None:
        updated.duration_seconds = extraction.moving_duration_seconds
        summary["session"]["duration_moving_s"] = extraction.moving_duration_seconds
        summary["estimates"]["estimated_duration_moving_s"] = extraction.moving_duration_seconds
        summary["estimates"]["estimated_duration_moving_s_confidence"] = (
            extraction.moving_duration_seconds_confidence
        )
    elif extraction.elapsed_duration_seconds is not None:
        updated.duration_seconds = extraction.elapsed_duration_seconds
        summary["session"]["duration_elapsed_s"] = extraction.elapsed_duration_seconds
        summary["estimates"]["estimated_duration_elapsed_s_confidence"] = (
            extraction.elapsed_duration_seconds_confidence
        )


def _apply_text_update_stream_fields(
    updated: Activity,
    summary: dict[str, Any],
    extraction: ActivityTextExtraction,
) -> None:
    if extraction.avg_hr_bpm is not None:
        updated.avg_hr_bpm = extraction.avg_hr_bpm
        summary["heart_rate"]["avg_bpm"] = extraction.avg_hr_bpm
        summary["estimates"]["estimated_avg_hr_bpm_confidence"] = extraction.avg_hr_bpm_confidence
        summary["data_quality"]["has_hr"] = True
    if extraction.max_hr_bpm is not None:
        updated.max_hr_bpm = extraction.max_hr_bpm
        summary["heart_rate"]["max_bpm"] = extraction.max_hr_bpm
        summary["estimates"]["estimated_max_hr_bpm_confidence"] = extraction.max_hr_bpm_confidence
        summary["data_quality"]["has_hr"] = True
    if extraction.avg_power_watts is not None:
        updated.avg_power_watts = extraction.avg_power_watts
        summary["power"]["avg_w"] = extraction.avg_power_watts
        summary["estimates"]["estimated_avg_power_watts_confidence"] = (
            extraction.avg_power_watts_confidence
        )
        summary["data_quality"]["has_power"] = True
    if extraction.normalized_power_watts is not None:
        updated.normalized_power_watts = extraction.normalized_power_watts
        summary["power"]["normalized_w"] = extraction.normalized_power_watts
        summary["estimates"]["estimated_normalized_power_watts_confidence"] = (
            extraction.normalized_power_watts_confidence
        )
        summary["data_quality"]["has_power"] = True


def _apply_text_update_fields(
    updated: Activity, summary: dict[str, Any], extraction: ActivityTextExtraction
) -> None:
    _apply_text_update_session_fields(updated, summary, extraction)
    _apply_text_update_stream_fields(updated, summary, extraction)
    if extraction.rpe is not None:
        updated.rpe = extraction.rpe
        summary["subjective"]["rpe_1_10"] = extraction.rpe
        summary["estimates"]["estimated_rpe_confidence"] = extraction.rpe_confidence
    fueling_notes = _fueling_notes(extraction)
    if fueling_notes is not None:
        updated.fueling_notes = fueling_notes
    if extraction.athlete_notes is not None:
        updated.athlete_notes = extraction.athlete_notes
    _merge_context_summary(summary, extraction)


async def merge_activity_text_update(
    existing: Activity,
    text: str,
    *,
    extractor: Extractor | None = None,
) -> Activity:
    extraction = await (extractor or extract_activity_text)(text)
    updated = existing.model_copy(deep=True)
    summary = _activity_summary_for_update(existing)
    _apply_text_update_fields(updated, summary, extraction)

    raw_extraction = copy.deepcopy(updated.raw_extraction) if updated.raw_extraction else {}
    text_updates = raw_extraction.setdefault("text_updates", [])
    if isinstance(text_updates, list):
        text_updates.append(
            {
                "input_text": text,
                "openai_extraction": extraction.model_dump(mode="json", exclude_none=True),
                "source": "text_extract",
            }
        )
    updated.raw_extraction = raw_extraction
    updated.summary_schema_version = SUMMARY_SCHEMA_VERSION
    updated.activity_summary = summary
    return updated


def _fueling_notes(extraction: ActivityTextExtraction) -> str | None:
    carbs = sum(item.carbs_g or 0 for item in extraction.nutrition_estimates)
    calories = sum(item.calories_kcal or 0 for item in extraction.nutrition_estimates)
    if carbs > 0 and calories > 0:
        return f"estimated {round(carbs):g} g carbs, {round(calories):g} kcal"
    if carbs > 0:
        return f"estimated {round(carbs):g} g carbs"
    if calories > 0:
        return f"estimated {round(calories):g} kcal"
    return None
