import os
from datetime import date

import pytest

from backend.engine.activity_text import (
    ActivityTextExtraction,
    ActivityTextExtractionUnavailable,
    AdditionalImportantData,
    ExtractedFoodItem,
    NutritionEstimate,
    build_activity_from_text,
    merge_activity_text_update,
)
from backend.models.athlete import AthleteProfile, SportThreshold
from backend.models.training import Activity

ISSUE_209_TEXT = (
    "Volunteer Park crit, Sat 13 Jun 2026 — 45 min race start at ~12:56-13:00. "
    "Report: in race ~19 minutes then blew up; avg HR 183 bpm, max 193 bpm; "
    "avg power 198 W, NP 243 W; CHO used ~103 g; short high-power surges up to "
    "~450 W for 8-15s; felt competitive for first 19 minutes. Requesting debrief "
    "and next steps."
)

_RUN_OAI_TESTS = os.environ.get("RUN_OAI_TESTS") == "1"
_OPENAI_CONFIGURED = bool(os.environ.get("OPENAI_API_KEY"))


@pytest.mark.asyncio
async def test_build_activity_from_text_extracts_rich_summary_and_estimates() -> None:
    profile = AthleteProfile(
        user_id="athlete-1",
        max_hr_bpm=195,
        resting_hr_bpm=52,
    )
    thresholds = [
        SportThreshold(
            user_id="athlete-1",
            sport="cycling",
            lt1_power_watts=180,
            lt2_power_watts=250,
            lt1_hr_bpm=145,
            lt2_hr_bpm=174,
        )
    ]

    async def fake_extractor(_text: str) -> ActivityTextExtraction:
        return ActivityTextExtraction(
            activity_date="2026-06-13",
            activity_date_confidence=0.9,
            additional_important_data=[
                AdditionalImportantData(
                    key="race_context",
                    value="blew up after about 19 minutes but felt competitive before that",
                    confidence=0.86,
                )
            ],
            athlete_notes="felt competitive for first 19 minutes",
            athlete_notes_confidence=0.88,
            avg_hr_bpm=183,
            avg_hr_bpm_confidence=0.95,
            avg_power_watts=198,
            avg_power_watts_confidence=0.95,
            best_power_watts=450,
            best_power_watts_confidence=0.55,
            best_power_window_seconds=15,
            best_power_window_seconds_confidence=0.6,
            elapsed_duration_seconds=45 * 60,
            elapsed_duration_seconds_confidence=0.82,
            food_items=[
                ExtractedFoodItem(
                    brand_hint=None,
                    confidence=0.65,
                    name="energy gel",
                    quantity=2,
                    serving_hint="2 generic gels",
                    timing_hint="during race",
                )
            ],
            max_hr_bpm=193,
            max_hr_bpm_confidence=0.95,
            moving_duration_seconds=19 * 60,
            moving_duration_seconds_confidence=0.86,
            normalized_power_watts=243,
            normalized_power_watts_confidence=0.95,
            nutrition_estimates=[
                NutritionEstimate(
                    calories_kcal=200,
                    calories_kcal_confidence=0.55,
                    carbs_g=50,
                    carbs_g_confidence=0.55,
                    item_name="2 generic energy gels",
                    source_title="Typical sports gel nutrition",
                    source_url="https://example.com/sports-gel-nutrition",
                )
            ],
            sport="cycling",
            sport_confidence=0.86,
            sub_sport="criterium",
            sub_sport_confidence=0.84,
        )

    result = await build_activity_from_text(
        ISSUE_209_TEXT,
        user_id="athlete-1",
        profile=profile,
        thresholds=thresholds,
        extractor=fake_extractor,
    )

    assert result.missing == []
    assert result.activity is not None
    activity = result.activity
    assert activity.sport == "cycling"
    assert activity.activity_date == date(2026, 6, 13)
    assert activity.duration_seconds == 19 * 60
    assert activity.avg_hr_bpm == 183
    assert activity.max_hr_bpm == 193
    assert activity.avg_power_watts == 198
    assert activity.normalized_power_watts == 243
    assert activity.source == "text_extract"
    assert activity.summary_schema_version == 1
    assert activity.intensity_factor == 0.97
    assert activity.tss == 29.9

    summary = activity.activity_summary
    assert summary["session"]["sub_sport"] == "criterium"
    assert summary["session"]["duration_elapsed_s"] == 45 * 60
    assert summary["estimates"]["estimated_sport"] == "cycling"
    assert summary["estimates"]["estimated_sport_confidence"] >= 0.8
    assert summary["estimates"]["estimated_duration_moving_s"] == 19 * 60
    assert summary["estimates"]["estimated_duration_moving_s_confidence"] >= 0.8
    assert summary["thresholds_used"]["ftp_w"] == 250
    assert summary["power"]["bests_w"]["15s"] == 450
    assert summary["load"]["primary_load"] == 29.9
    assert summary["load"]["tss_power"] == 29.9
    assert summary["load"]["work_kj"] == 225.7
    assert summary["fueling"]["carbs_g"] == 50
    assert summary["fueling"]["carbs_g_confidence"] == 0.55
    assert summary["fueling"]["calories_kcal"] == 200
    assert summary["fueling"]["calories_kcal_confidence"] == 0.55
    assert "gels_count" not in summary["fueling"]
    assert summary["food_items"][0]["name"] == "energy gel"
    assert summary["food_items"][0]["confidence"] == 0.65
    assert summary["additional_important_data"][0]["key"] == "race_context"
    assert summary["subjective"]["athlete_notes"].startswith("felt competitive")
    assert summary["data_quality"] == {
        "source": "text_extract",
        "has_power": True,
        "has_hr": True,
        "has_gps": False,
        "has_rr_intervals": False,
        "estimated_from_text": True,
    }
    assert activity.raw_extraction is not None
    assert activity.raw_extraction["input_text"] == ISSUE_209_TEXT
    assert activity.raw_extraction["openai_extraction"]["sport"] == "cycling"


@pytest.mark.asyncio
async def test_build_activity_from_text_requires_openai_extraction() -> None:
    async def failing_extractor(_text: str) -> ActivityTextExtraction:
        raise ActivityTextExtractionUnavailable("OpenAI activity text extraction unavailable.")

    with pytest.raises(ActivityTextExtractionUnavailable):
        await build_activity_from_text(
            "Ran yesterday and felt okay.",
            user_id="athlete-1",
            profile=AthleteProfile(user_id="athlete-1"),
            thresholds=[],
            extractor=failing_extractor,
        )


@pytest.mark.asyncio
async def test_merge_activity_text_update_preserves_original_source_and_adds_estimates() -> None:
    existing = Activity(
        id="activity-1",
        user_id="athlete-1",
        sport="cycling",
        activity_date=date(2026, 6, 13),
        source="fit_upload",
        activity_summary={
            "schema": "activity_summary_v1",
            "session": {"sport": "cycling"},
            "fueling": {},
            "subjective": {},
            "data_quality": {"source": "fit_upload"},
        },
        raw_extraction={"filename": "race.fit"},
    )

    async def fake_extractor(_text: str) -> ActivityTextExtraction:
        return ActivityTextExtraction(
            activity_date=None,
            activity_date_confidence=None,
            additional_important_data=[
                AdditionalImportantData(key="overreach", value="overdid it", confidence=0.9)
            ],
            food_items=[
                ExtractedFoodItem(
                    brand_hint=None,
                    confidence=0.6,
                    name="energy gel",
                    quantity=2,
                    serving_hint="2 generic gels",
                    timing_hint=None,
                )
            ],
            gut_comfort_1_10=8,
            gut_comfort_1_10_confidence=0.8,
            nutrition_estimates=[
                NutritionEstimate(
                    calories_kcal=200,
                    calories_kcal_confidence=0.5,
                    carbs_g=50,
                    carbs_g_confidence=0.5,
                    item_name="2 generic energy gels",
                    source_title=None,
                    source_url=None,
                )
            ],
            overdid_it_flag=True,
            overdid_it_flag_confidence=0.9,
            rpe=9,
            rpe_confidence=0.8,
        )

    updated = await merge_activity_text_update(
        existing,
        "Add that I took 2 gels, gut felt 8/10, RPE 9, and I overdid it.",
        extractor=fake_extractor,
    )

    assert updated.id == "activity-1"
    assert updated.source == "fit_upload"
    assert updated.rpe == 9
    assert updated.fueling_notes == "estimated 50 g carbs, 200 kcal"
    assert updated.activity_summary["fueling"]["carbs_g"] == 50
    assert updated.activity_summary["fueling"]["calories_kcal"] == 200
    assert "gels_count" not in updated.activity_summary["fueling"]
    assert updated.activity_summary["food_items"][0]["name"] == "energy gel"
    assert updated.activity_summary["fueling"]["gut_comfort_1_10"] == 8
    assert updated.activity_summary["subjective"]["rpe_1_10"] == 9
    assert updated.activity_summary["subjective"]["overdid_it_flag"] is True
    assert updated.activity_summary["data_quality"]["source"] == "fit_upload"
    assert updated.raw_extraction is not None
    assert updated.raw_extraction["text_updates"][-1]["source"] == "text_extract"


@pytest.mark.skipif(
    not (_RUN_OAI_TESTS and _OPENAI_CONFIGURED),
    reason="RUN_OAI_TESTS=1 and OPENAI_API_KEY are required for live OpenAI extraction.",
)
@pytest.mark.asyncio
async def test_extract_activity_text_live_openai_returns_food_and_confidence() -> None:
    from backend.engine.activity_text import extract_activity_text

    extraction = await extract_activity_text(
        "I rode a hard 52 minute lunch crit today. I ate one Maurten Gel 100 "
        "and drank half a bottle of Skratch."
    )

    assert extraction.sport in {"cycling", "ride"}
    assert extraction.sport_confidence is not None
    assert extraction.sport_confidence >= 0.5
    assert extraction.food_items
    assert any("gel" in item.name.lower() for item in extraction.food_items)
    assert extraction.nutrition_estimates
    assert sum(item.carbs_g or 0 for item in extraction.nutrition_estimates) > 0
