"""Screenshot classification and data extraction via vision model.

Two-step process:
1. Classify screenshot type (activity, wellness multi-day, wellness single-day, etc.)
2. Route to type-specific extraction prompt
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from backend.config import get_settings

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

Respond with ONLY a JSON object:
{
  "screenshot_type": "<category>",
  "source_app_hint": "<app name or null>",
  "date_range_hint": "<date or range or null>",
  "confidence": <0.0-1.0>
}
"""

EXTRACT_ACTIVITY_PROMPT = """Extract workout data from this activity screenshot.
Return a JSON object with these fields (use null for any field not visible):

{
  "sport": "running|cycling|swimming|rowing|hiking|general",
  "activity_date": "YYYY-MM-DD",
  "duration_seconds": <integer>,
  "distance_meters": <number>,
  "elevation_gain_meters": <number>,
  "avg_hr_bpm": <integer>,
  "max_hr_bpm": <integer>,
  "avg_power_watts": <integer>,
  "normalized_power_watts": <integer>,
  "avg_pace_sec_per_km": <integer>,
  "avg_cadence_rpm": <integer>,
  "tss": <number if shown>,
  "confidence": {"sport": 0.9, "duration": 0.95, ...}
}

Only include fields you can clearly read from the screenshot. Do not guess values."""

EXTRACT_WELLNESS_MULTI_PROMPT = """Extract daily wellness/recovery data from this screenshot.
It may show multiple days.

Return a JSON object:
{
  "entries": [
    {
      "date": "YYYY-MM-DD",
      "sleep_duration_hours": <number>,
      "sleep_score": <integer 0-100>,
      "hrv_ms": <number>,
      "resting_hr_bpm": <integer>,
      "body_battery": <integer 0-100>,
      "stress_score": <integer>,
      "confidence": <0.0-1.0>
    }
  ]
}

Extract one entry per visible day. Use null for fields not shown. Do not guess values."""

EXTRACT_WELLNESS_SINGLE_PROMPT = """Extract today's wellness/recovery data from this screenshot.

Return a JSON object:
{
  "date": "YYYY-MM-DD",
  "sleep_duration_hours": <number>,
  "sleep_score": <integer 0-100>,
  "sleep_consistency_pct": <number>,
  "hrv_ms": <number>,
  "resting_hr_bpm": <integer>,
  "body_battery": <integer 0-100>,
  "stress_score": <integer>,
  "subjective_energy": <integer 1-5 if shown>,
  "confidence": <0.0-1.0>
}

Use null for fields not visible. Do not guess."""


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
    response = await _call_vision(CLASSIFY_PROMPT, image_url)
    import json

    try:
        parsed = json.loads(response)
    except json.JSONDecodeError:
        return ScreenshotClassification(
            screenshot_type="unknown",
            source_app_hint=None,
            date_range_hint=None,
            confidence=0.0,
        )

    return ScreenshotClassification(
        screenshot_type=parsed.get("screenshot_type", "unknown"),
        source_app_hint=parsed.get("source_app_hint"),
        date_range_hint=parsed.get("date_range_hint"),
        confidence=parsed.get("confidence", 0.0),
    )


async def extract_from_screenshot(
    image_url: str,
    screenshot_type: str,
) -> ExtractionResult:
    """Step 2: Extract structured data based on classification."""
    prompt_map = {
        "activity_single": EXTRACT_ACTIVITY_PROMPT,
        "wellness_multi_day": EXTRACT_WELLNESS_MULTI_PROMPT,
        "wellness_single_day": EXTRACT_WELLNESS_SINGLE_PROMPT,
    }

    prompt = prompt_map.get(screenshot_type)
    if prompt is None:
        return ExtractionResult(
            screenshot_type=screenshot_type,
            data={},
            raw_response="Unsupported screenshot type for extraction.",
        )

    response = await _call_vision(prompt, image_url)
    import json

    try:
        data = json.loads(response)
    except json.JSONDecodeError:
        data = {"raw_text": response}

    return ExtractionResult(
        screenshot_type=screenshot_type,
        data=data,
        raw_response=response,
    )


async def analyze_screenshot(image_url: str) -> ExtractionResult:
    """Full pipeline: classify then extract."""
    classification = await classify_screenshot(image_url)

    if (
        classification.screenshot_type == "unknown"
        or classification.confidence < MIN_SCREENSHOT_CLASSIFICATION_CONFIDENCE
    ):
        return ExtractionResult(
            screenshot_type="unknown",
            data={"classification": classification.__dict__},
            raw_response="Could not confidently classify this screenshot.",
        )

    result = await extract_from_screenshot(image_url, classification.screenshot_type)
    result.data["classification"] = classification.__dict__
    return result


async def _call_vision(prompt: str, image_url: str) -> str:
    """Call OpenAI vision API with an image URL."""
    settings = get_settings()
    if not settings.openai_api_key:
        return "{}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4.1-mini",
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {"type": "input_image", "image_url": image_url},
                        ],
                    }
                ],
            },
        )
        resp.raise_for_status()
        data = resp.json()

    # Extract text from response
    for item in data.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    return content.get("text", "{}")
    return "{}"
