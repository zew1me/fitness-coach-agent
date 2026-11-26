"""Compose measurement fields from the live evidence of one activity."""

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend.models.training import ActivityIngestFormat, ActivitySource

_MEASUREMENT_FIELDS = (
    "sport",
    "activity_date",
    "started_at",
    "duration_seconds",
    "distance_meters",
    "elevation_gain_meters",
    "avg_hr_bpm",
    "max_hr_bpm",
    "avg_power_watts",
    "normalized_power_watts",
    "avg_pace_sec_per_km",
    "avg_cadence_rpm",
    "zone_distribution",
)


@dataclass(frozen=True)
class ActivityMeasurementProjection:
    fields: dict[str, Any]
    field_provenance: dict[str, str]


def compose_activity_measurements(
    sources: Iterable[ActivitySource],
    fidelity_ranks: Mapping[ActivityIngestFormat, int],
) -> ActivityMeasurementProjection:
    """Pick the first non-null value per field in fidelity, creation-time, ID order.

    Ranks come from ``activity_ingest_formats`` at the I/O boundary. Unresolved
    fields are absent from both dictionaries; this is not a complete write payload.
    In particular, ``general`` is undeclared sport, not a value that can win.
    Derived metrics, athlete notes, and the legacy provenance triplet are separate.
    """
    live_sources = [source for source in sources if source.retired_at is None]
    if len({(source.user_id, source.activity_id) for source in live_sources}) > 1:
        raise ValueError("A projection requires live sources of one athlete's activity.")

    def precedence(source: ActivitySource) -> tuple[int, datetime, str]:
        rank = fidelity_ranks.get(source.ingest_format)
        if rank is None:
            raise ValueError(f"Missing fidelity rank for {source.ingest_format}.")
        created_at = source.created_at
        if created_at is None or created_at.utcoffset() is None:
            raise ValueError("A live activity source requires a timezone-aware creation time.")
        return rank, created_at.astimezone(UTC), source.id

    fields: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    for source in sorted(live_sources, key=precedence):
        for field in _MEASUREMENT_FIELDS:
            value = source.fields.get(field)
            if field in fields or value is None or (field == "sport" and value == "general"):
                continue
            fields[field] = deepcopy(value)
            provenance[field] = source.id
    return ActivityMeasurementProjection(fields, provenance)
