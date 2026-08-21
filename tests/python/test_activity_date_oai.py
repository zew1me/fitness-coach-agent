"""Live OpenAI evaluations for activity-date correction safeguards."""

import os
from datetime import UTC, date, datetime
from typing import Literal

import pytest

from backend.models.athlete import AthleteProfile
from backend.models.training import Activity
from backend.services.activity_text import (
    ActivityTextExtraction,
    ActivityTextUpdateResult,
    RefusedDateEditReason,
    extract_activity_text,
    merge_activity_text_update,
)

pytestmark = pytest.mark.oai

_OPENAI_CONFIGURED = bool(os.environ.get("OPENAI_API_KEY"))


async def _merge_extraction(
    existing: Activity,
    extraction: ActivityTextExtraction,
    message: str,
) -> ActivityTextUpdateResult:
    async def evaluated_extractor(_text: str) -> ActivityTextExtraction:
        return extraction

    return await merge_activity_text_update(
        existing,
        message,
        profile=AthleteProfile(user_id=existing.user_id),
        thresholds=[],
        extractor=evaluated_extractor,
    )


@pytest.mark.skipif(
    not _OPENAI_CONFIGURED,
    reason="OPENAI_API_KEY is required for live date evaluations.",
)
@pytest.mark.asyncio
async def test_live_openai_date_correction_covers_accepted_and_refused_verdicts() -> None:
    """One real extraction must drive every valid-date edit verdict deterministically."""
    message = "Correction: this activity happened on July 5, 2026, not July 6."
    extraction = await extract_activity_text(message)

    assert extraction.activity_date == "2026-07-05"
    assert extraction.activity_date_confidence is not None
    assert extraction.activity_date_confidence >= 0.9

    cases: list[
        tuple[
            Literal["applied"] | RefusedDateEditReason,
            Activity,
            date,
        ]
    ] = [
        (
            "applied",
            Activity(
                id="oai-date-applied",
                user_id="athlete-oai-date",
                sport="running",
                activity_date=date(2026, 7, 6),
                source="text_extract",
            ),
            date(2026, 7, 5),
        ),
        (
            "refused_authoritative",
            Activity(
                id="oai-date-authoritative",
                user_id="athlete-oai-date",
                sport="running",
                activity_date=date(2026, 7, 6),
                source="fit_upload",
                raw_extraction={"activity_date_source": "fit_local_timestamp"},
            ),
            date(2026, 7, 6),
        ),
        (
            "refused_implausible",
            Activity(
                id="oai-date-implausible",
                user_id="athlete-oai-date",
                sport="running",
                activity_date=date(2026, 7, 7),
                started_at=datetime(2026, 7, 7, 12, tzinfo=UTC),
                source="fit_upload",
            ),
            date(2026, 7, 7),
        ),
    ]

    observed: set[Literal["applied"] | RefusedDateEditReason] = set()
    for expected_verdict, existing, expected_date in cases:
        result = await _merge_extraction(existing, extraction, message)
        assert result.activity.activity_date == expected_date
        verdict: Literal["applied"] | RefusedDateEditReason = (
            result.rejected_updates[0]["reason"] if result.rejected_updates else "applied"
        )
        assert verdict == expected_verdict
        observed.add(verdict)

    assert observed == {"applied", "refused_authoritative", "refused_implausible"}


@pytest.mark.skipif(
    not _OPENAI_CONFIGURED,
    reason="OPENAI_API_KEY is required for live date evaluations.",
)
@pytest.mark.asyncio
async def test_live_openai_invalid_date_is_refused() -> None:
    message = (
        "Correction: the activity date field should be exactly 2026-02-30. "
        "Use that literal date from my records without changing it."
    )
    extraction = await extract_activity_text(message)

    assert extraction.activity_date == "2026-02-30"
    assert extraction.activity_date_confidence is not None
    assert extraction.activity_date_confidence >= 0.9

    existing = Activity(
        id="oai-date-invalid",
        user_id="athlete-oai-date",
        sport="running",
        activity_date=date(2026, 2, 28),
        source="text_extract",
    )
    result = await _merge_extraction(existing, extraction, message)

    assert result.activity.activity_date == date(2026, 2, 28)
    assert result.rejected_updates == [
        {
            "field": "activity_date",
            "value": "2026-02-30",
            "reason": "refused_invalid",
        }
    ]
