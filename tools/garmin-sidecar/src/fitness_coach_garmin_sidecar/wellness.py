"""Collect and format Garmin wellness data for coach-chat ingestion."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol


class GarminWellnessClient(Protocol):
    """Narrow subset of python-garminconnect used by wellness exports."""

    def get_sleep_data(self, cdate: str) -> dict[str, Any]: ...

    def get_hrv_data(self, cdate: str) -> dict[str, Any] | None: ...

    def get_body_battery(
        self, startdate: str, enddate: str | None = None
    ) -> list[dict[str, Any]]: ...

    def get_rhr_day(self, cdate: str) -> dict[str, Any]: ...

    def get_user_summary(self, cdate: str) -> dict[str, Any]: ...


WellnessValue = str | int | float
WellnessRow = dict[str, WellnessValue]
_BODY_BATTERY_SAMPLE_SIZE = 2
_WELLNESS_FIELD_ORDER = (
    "log_date",
    "sleep_duration_hours",
    "sleep_score",
    "sleep_consistency_pct",
    "hrv_ms",
    "resting_hr_bpm",
    "body_battery",
    "stress_score",
)


@dataclass(frozen=True)
class WellnessFailure:
    """One metric request that failed for one date."""

    log_date: date
    metric: str
    message: str


@dataclass
class WellnessExportSummary:
    """Rows and recoverable failures for an inclusive wellness export."""

    rows: list[WellnessRow] = field(default_factory=list)
    failures: list[WellnessFailure] = field(default_factory=list)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _bounded_number(
    value: Any, *, minimum: float | None = None, maximum: float | None = None
) -> int | float | None:
    number = _number(value)
    if number is None or (minimum is not None and number < minimum):
        return None
    if maximum is not None and number > maximum:
        return None
    return number


def _positive_int(value: Any) -> int | None:
    number = _number(value)
    if number is None or number <= 0 or not float(number).is_integer():
        return None
    return int(number)


def _score_value(value: Any) -> int | float | None:
    score = _mapping(value).get("value") if isinstance(value, Mapping) else value
    return _bounded_number(score, minimum=0)


def _sleep_fields(payload: Any) -> WellnessRow:
    daily_sleep = _mapping(_mapping(payload).get("dailySleepDTO"))
    sleep_scores = _mapping(daily_sleep.get("sleepScores"))
    fields: WellnessRow = {}

    seconds = _bounded_number(daily_sleep.get("sleepTimeSeconds"), minimum=0)
    if seconds is not None:
        fields["sleep_duration_hours"] = round(seconds / 3600, 1)

    sleep_score = _score_value(sleep_scores.get("overall"))
    if sleep_score is not None:
        fields["sleep_score"] = sleep_score

    consistency = _score_value(sleep_scores.get("sleepConsistency"))
    consistency = _bounded_number(consistency, minimum=0, maximum=100)
    if consistency is not None:
        fields["sleep_consistency_pct"] = consistency
    return fields


def _hrv_fields(payload: Any) -> WellnessRow:
    last_night_avg = _bounded_number(
        _mapping(_mapping(payload).get("hrvSummary")).get("lastNightAvg"), minimum=0
    )
    return {"hrv_ms": last_night_avg} if last_night_avg is not None else {}


def _body_battery_fields(payload: Any, *, log_date: date) -> WellnessRow:
    if not isinstance(payload, Sequence) or isinstance(payload, str | bytes):
        return {}

    requested_date = log_date.isoformat()
    entries = [entry for entry in payload if isinstance(entry, Mapping)]
    dated_entries = [entry for entry in entries if entry.get("date") == requested_date]
    levels: list[int] = []
    for entry in dated_entries:
        values = entry.get("bodyBatteryValuesArray")
        if not isinstance(values, Sequence) or isinstance(values, str | bytes):
            continue
        for sample in values:
            if not isinstance(sample, Sequence) or isinstance(sample, str | bytes):
                continue
            if len(sample) < _BODY_BATTERY_SAMPLE_SIZE:
                continue
            level = _bounded_number(sample[1], minimum=0, maximum=100)
            if level is not None and float(level).is_integer():
                levels.append(int(level))
    return {"body_battery": max(levels)} if levels else {}


def _rhr_fields(payload: Any, *, log_date: date) -> WellnessRow:
    metrics_map = _mapping(_mapping(_mapping(payload).get("allMetrics")).get("metricsMap"))
    readings = metrics_map.get("WELLNESS_RESTING_HEART_RATE")
    if not isinstance(readings, Sequence) or isinstance(readings, str | bytes):
        return {}

    requested_date = log_date.isoformat()
    dated_readings = [
        reading
        for reading in readings
        if isinstance(reading, Mapping) and reading.get("calendarDate") == requested_date
    ]
    for reading in dated_readings:
        resting_hr = _positive_int(reading.get("value"))
        if resting_hr is not None:
            return {"resting_hr_bpm": resting_hr}
    return {}


def _summary_fields(payload: Any, *, include_resting_hr: bool) -> WellnessRow:
    summary = _mapping(payload)
    fields: WellnessRow = {}
    stress = _bounded_number(summary.get("averageStressLevel"), minimum=0)
    if stress is not None:
        fields["stress_score"] = stress
    if include_resting_hr:
        resting_hr = _positive_int(summary.get("restingHeartRate"))
        if resting_hr is not None:
            fields["resting_hr_bpm"] = resting_hr
    return fields


def _request_metric(
    summary: WellnessExportSummary,
    *,
    log_date: date,
    metric: str,
    request: Callable[[], Any],
) -> Any:
    try:
        return request()
    except Exception as exc:
        summary.failures.append(WellnessFailure(log_date=log_date, metric=metric, message=str(exc)))
        return None


def collect_wellness(
    client: GarminWellnessClient, *, start: date, end: date
) -> WellnessExportSummary:
    """Collect supported recovery fields for every day, newest first."""
    summary = WellnessExportSummary()
    log_date = end
    while log_date >= start:
        cdate = log_date.isoformat()
        row: WellnessRow = {"log_date": cdate}

        sleep = _request_metric(
            summary,
            log_date=log_date,
            metric="sleep",
            request=lambda cdate=cdate: client.get_sleep_data(cdate),
        )
        row.update(_sleep_fields(sleep))

        hrv = _request_metric(
            summary,
            log_date=log_date,
            metric="hrv",
            request=lambda cdate=cdate: client.get_hrv_data(cdate),
        )
        row.update(_hrv_fields(hrv))

        body_battery = _request_metric(
            summary,
            log_date=log_date,
            metric="body_battery",
            request=lambda cdate=cdate: client.get_body_battery(cdate, cdate),
        )
        row.update(_body_battery_fields(body_battery, log_date=log_date))

        resting_hr = _request_metric(
            summary,
            log_date=log_date,
            metric="resting_hr",
            request=lambda cdate=cdate: client.get_rhr_day(cdate),
        )
        row.update(_rhr_fields(resting_hr, log_date=log_date))

        daily_summary = _request_metric(
            summary,
            log_date=log_date,
            metric="daily_summary",
            request=lambda cdate=cdate: client.get_user_summary(cdate),
        )
        row.update(_summary_fields(daily_summary, include_resting_hr="resting_hr_bpm" not in row))

        summary.rows.append({key: row[key] for key in _WELLNESS_FIELD_ORDER if key in row})
        log_date -= timedelta(days=1)

    return summary


def local_timezone_name() -> str:
    """Return a stable local timezone label without making another Garmin request."""
    configured = os.environ.get("TZ", "").lstrip(":")
    if configured:
        return " ".join(configured.split())

    localtime = Path("/etc/localtime")
    if localtime.exists():
        resolved = str(localtime.resolve())
        marker = "/zoneinfo/"
        if marker in resolved:
            return resolved.split(marker, 1)[1]

    fallback = str(datetime.now().astimezone().tzinfo or "UTC")
    return " ".join(fallback.split()) or "UTC"


def format_wellness_text(
    rows: Sequence[Mapping[str, WellnessValue]],
    *,
    start: date,
    end: date,
    timezone_name: str,
) -> str:
    """Format rows as the load-bearing paste-into-chat block."""
    lines = [
        "=== WELLNESS EXPORT v1 source=garmin_sidecar ===",
        f"dates: {start.isoformat()}..{end.isoformat()}  timezone: {timezone_name}",
    ]
    for row in rows:
        fields = [
            f"{key}={value:g}" if isinstance(value, float) else f"{key}={value}"
            for key, value in row.items()
        ]
        lines.append(" ".join(fields))
    lines.append("=== END WELLNESS EXPORT ===")
    return "\n".join(lines)


def format_wellness_json(rows: Sequence[Mapping[str, WellnessValue]]) -> str:
    """Format the same export rows as JSON for automation."""
    return json.dumps(list(rows), indent=2)
