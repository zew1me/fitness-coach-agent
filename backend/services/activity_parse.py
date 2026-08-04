"""Shared lenient parsers for provider activity payloads.

Both the Intervals.icu and Strava mappers ingest loosely-typed JSON summaries and
must coerce optional numeric/date fields without raising on missing or malformed
values. These helpers are the single source of that coercion.
"""

from __future__ import annotations

from datetime import date, datetime

ISO_DATE_LENGTH = 10


def optional_date(value: object) -> date | None:
    if not isinstance(value, str) or len(value) < ISO_DATE_LENGTH:
        return None
    try:
        return date.fromisoformat(value[:ISO_DATE_LENGTH])
    except ValueError:
        return None


def optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def optional_int(value: object) -> int | None:
    number = optional_float(value)
    return round(number) if number is not None else None


def first_positive_int(*values: object) -> int | None:
    for value in values:
        number = optional_int(value)
        if number is not None and number > 0:
            return number
    return None
