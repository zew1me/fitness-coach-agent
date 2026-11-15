"""Map ``garmin-sidecar wellness export --format json`` rows to ``save-recovery-data`` entries.

The sidecar package is deliberately not imported: the two tools compose through a pipe, so
this module only encodes the (stable, documented) row shape.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from fitness_coach_cli.errors import CoachError

GARMIN_SOURCE = "garmin"
_INTEGER_FIELDS = ("sleep_score", "resting_hr_bpm", "body_battery", "stress_score")
_FLOAT_FIELDS = ("sleep_duration_hours", "sleep_consistency_pct", "hrv_ms")
_SUPPORTED = frozenset({"log_date", *_INTEGER_FIELDS, *_FLOAT_FIELDS})


@dataclass
class WellnessMapping:
    entries: list[dict[str, Any]] = field(default_factory=list)
    dropped_fields: set[str] = field(default_factory=set)
    skipped_dates: list[str] = field(default_factory=list)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _log_date(row: dict[str, Any]) -> str:
    raw = row.get("log_date")
    try:
        return date.fromisoformat(str(raw)).isoformat()
    except ValueError as exc:
        raise CoachError("invalid_wellness_input", f"Row has no valid log_date: {raw!r}") from exc


def _map_row(row: dict[str, Any], result: WellnessMapping) -> None:
    log_date = _log_date(row)
    entry: dict[str, Any] = {"log_date": log_date, "source": GARMIN_SOURCE}
    for key, value in row.items():
        if key not in _SUPPORTED:
            result.dropped_fields.add(key)
        elif key == "log_date":
            continue
        elif not _is_number(value):
            result.dropped_fields.add(key)
        # The backend types these as int; the sidecar may emit 82.0 or an averaged float.
        elif key in _INTEGER_FIELDS:
            entry[key] = round(value)
        else:
            entry[key] = value
    if set(entry) == {"log_date", "source"}:
        result.skipped_dates.append(log_date)  # nothing measured; do not create an empty row
    else:
        result.entries.append(entry)


def map_sidecar_rows(rows: Any) -> WellnessMapping:
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise CoachError(
            "invalid_wellness_input",
            "Expected a JSON array of rows from `garmin-sidecar wellness export --format json`.",
        )
    result = WellnessMapping()
    for row in rows:
        _map_row(row, result)
    return result


def parse_sidecar_json(text: str) -> WellnessMapping:
    try:
        rows = json.loads(text)
    except ValueError as exc:
        raise CoachError("invalid_wellness_input", f"Input is not valid JSON: {exc}") from exc
    return map_sidecar_rows(rows)
