from datetime import UTC, datetime, timedelta, timezone
from itertools import permutations
from typing import Any

import pytest

from backend.engine.activity_projection import compose_activity_measurements
from backend.models.training import ActivityIngestFormat, ActivitySource

_CREATED_AT = datetime(2026, 11, 25, tzinfo=UTC)
_RANKS: dict[ActivityIngestFormat, int] = {"fit": 1, "intervals_api": 4}


def _source(
    source_id: str,
    ingest_format: ActivityIngestFormat,
    fields: dict[str, Any],
) -> ActivitySource:
    return ActivitySource(
        id=source_id,
        activity_id="activity",
        origin_activity_id="activity",
        user_id="athlete",
        provider="unknown",
        ingest_format=ingest_format,
        fields=fields,
        created_at=_CREATED_AT,
    )


def test_projection_selects_non_null_measurements_and_their_provenance() -> None:
    fit = _source(
        "fit",
        "fit",
        {
            "sport": "general",
            "activity_date": "2026-11-25",
            "started_at": "2026-11-25T19:00:00Z",
            "duration_seconds": 0,
            "avg_hr_bpm": None,
            "zone_distribution": {},
            "tss": 40,
            "rpe": 5,
            "athlete_notes": "Not a measurement",
            "planned_workout_id": "never projected",
        },
    )
    intervals = _source(
        "intervals",
        "intervals_api",
        {
            "sport": "cycling",
            "activity_date": "2026-11-24",
            "started_at": "2026-11-25T18:59:00Z",
            "duration_seconds": 3600,
            "distance_meters": 12000,
            "avg_hr_bpm": 140,
            "max_hr_bpm": None,
            "zone_distribution": {"2": 30},
            "tss": 52,
        },
    )

    projected = compose_activity_measurements([intervals, fit], _RANKS)

    assert projected.fields == {
        "sport": "cycling",
        "activity_date": "2026-11-25",
        "started_at": "2026-11-25T19:00:00Z",
        "duration_seconds": 0,
        "distance_meters": 12000,
        "avg_hr_bpm": 140,
        "zone_distribution": {},
    }
    assert projected.field_provenance == {
        "sport": "intervals",
        "activity_date": "fit",
        "started_at": "fit",
        "duration_seconds": "fit",
        "distance_meters": "intervals",
        "avg_hr_bpm": "intervals",
        "zone_distribution": "fit",
    }
    projected.fields["zone_distribution"]["2"] = 12
    assert fit.fields["zone_distribution"] == {}


def test_projection_breaks_ties_by_creation_instant_then_id_regardless_of_input_order() -> None:
    later = _source("a", "fit", {"distance_meters": 2})
    later.created_at = _CREATED_AT + timedelta(seconds=1)
    earlier_smaller_id = _source("b", "fit", {"distance_meters": 3})
    earlier_larger_id = _source("c", "fit", {"distance_meters": 4})
    earlier_larger_id.created_at = _CREATED_AT.astimezone(timezone(timedelta(hours=-8)))

    for sources in permutations((later, earlier_smaller_id, earlier_larger_id)):
        projected = compose_activity_measurements(sources, _RANKS)
        assert projected.fields == {"distance_meters": 3}
        assert projected.field_provenance == {"distance_meters": "b"}


def test_projection_uses_supplied_ranks_and_promotes_live_evidence_after_retirement() -> None:
    fit = _source("fit", "fit", {"sport": "general", "avg_power_watts": 100})
    intervals = _source("intervals", "intervals_api", {"avg_power_watts": 110})
    ranks: dict[ActivityIngestFormat, int] = {"fit": 2, "intervals_api": 1}

    assert compose_activity_measurements([fit, intervals], ranks).fields == {"avg_power_watts": 110}
    intervals.retired_at = _CREATED_AT
    projected = compose_activity_measurements([fit, intervals], ranks)
    assert projected.fields == {"avg_power_watts": 100}
    assert projected.field_provenance == {"avg_power_watts": "fit"}
    fit.retired_at = _CREATED_AT
    empty = compose_activity_measurements([fit, intervals], {})
    assert empty.fields == empty.field_provenance == {}


@pytest.mark.parametrize(
    "overrides",
    [{"activity_id": "another activity"}, {"user_id": "another athlete"}],
)
def test_projection_rejects_sources_from_different_live_memberships(
    overrides: dict[str, str],
) -> None:
    fit = _source("fit", "fit", {})
    other = _source("intervals", "intervals_api", {}).model_copy(update=overrides)
    with pytest.raises(ValueError, match="one athlete's activity"):
        compose_activity_measurements([fit, other], _RANKS)


def test_projection_rejects_missing_fidelity_rank() -> None:
    with pytest.raises(ValueError, match="Missing fidelity rank for fit"):
        compose_activity_measurements([_source("fit", "fit", {})], {})


@pytest.mark.parametrize("created_at", [None, datetime(2026, 11, 25)])
def test_projection_rejects_unknown_creation_instant(created_at: datetime | None) -> None:
    fit = _source("fit", "fit", {})
    fit.created_at = created_at
    with pytest.raises(ValueError, match="timezone-aware creation time"):
        compose_activity_measurements([fit], _RANKS)
