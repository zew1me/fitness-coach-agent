"""GPX, FIT, and TCX file parsing to structured activity data."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from backend.engine.hrv import HRVSummary, summarize_hrv

CYCLING_INFERRED_PACE_SEC_KM = 180
MIN_RR_INTERVAL_MS = 300
MAX_RR_INTERVAL_MS = 2000
SECONDS_TO_MS_THRESHOLD = 10


@dataclass
class ParsedActivity:
    """Structured activity data extracted from a workout activity file."""

    sport: str
    activity_date: date
    started_at: datetime | None = None
    duration_seconds: int | None = None
    distance_meters: float | None = None
    elevation_gain_meters: float | None = None
    avg_hr_bpm: int | None = None
    max_hr_bpm: int | None = None
    avg_power_watts: int | None = None
    avg_cadence_rpm: int | None = None
    power_stream: list[int] | None = None  # for NP calculation
    rr_intervals_ms: list[int] | None = None
    hrv_summary: HRVSummary | None = None


def parse_gpx(file_path: str | Path) -> ParsedActivity:  # noqa: C901, PLR0912
    """Parse a GPX file into structured activity data."""
    import gpxpy

    with Path(file_path).open() as f:
        gpx = gpxpy.parse(f)

    total_distance = 0.0
    total_elevation_gain = 0.0
    hr_values: list[int] = []
    power_values: list[int] = []
    cadence_values: list[int] = []
    rr_intervals: list[int] = []
    start_time: datetime | None = None
    end_time: datetime | None = None

    for track in gpx.tracks:
        for segment in track.segments:
            points = segment.points
            if not points:
                continue

            if start_time is None and points[0].time:
                start_time = points[0].time
            if points[-1].time:
                end_time = points[-1].time

            for i, point in enumerate(points):
                if i > 0:
                    total_distance += point.distance_2d(points[i - 1]) or 0
                    ele_diff = (point.elevation or 0) - (points[i - 1].elevation or 0)
                    if ele_diff > 0:
                        total_elevation_gain += ele_diff

                # Extract HR, power, cadence from extensions
                if point.extensions:
                    for ext in point.extensions:
                        _extract_gpx_extension(
                            ext,
                            hr_values,
                            power_values,
                            cadence_values,
                            rr_intervals,
                        )

    duration = None
    if start_time and end_time:
        duration = int((end_time - start_time).total_seconds())

    activity_date = start_time.date() if start_time else date.today()

    # Infer sport from average pace if available
    sport = "running"
    if duration and total_distance > 0:
        pace_sec_km = duration / (total_distance / 1000)
        if pace_sec_km < CYCLING_INFERRED_PACE_SEC_KM:  # faster than 3:00/km -> likely cycling
            sport = "cycling"

    return ParsedActivity(
        sport=sport,
        activity_date=activity_date,
        started_at=start_time,
        duration_seconds=duration,
        distance_meters=round(total_distance, 1) if total_distance > 0 else None,
        elevation_gain_meters=round(total_elevation_gain, 1) if total_elevation_gain > 0 else None,
        avg_hr_bpm=round(sum(hr_values) / len(hr_values)) if hr_values else None,
        max_hr_bpm=max(hr_values) if hr_values else None,
        avg_power_watts=round(sum(power_values) / len(power_values)) if power_values else None,
        avg_cadence_rpm=round(sum(cadence_values) / len(cadence_values))
        if cadence_values
        else None,
        power_stream=power_values if power_values else None,
        rr_intervals_ms=rr_intervals if rr_intervals else None,
        hrv_summary=summarize_hrv(rr_intervals) if rr_intervals else None,
    )


def _extract_gpx_extension(  # noqa: C901
    ext: Any,
    hr_values: list[int],
    power_values: list[int],
    cadence_values: list[int],
    rr_intervals: list[int],
) -> None:
    """Extract HR, power, cadence, and RR intervals from GPX extension elements."""
    tag = _local_name(ext.tag)

    if tag == "TrackPointExtension":
        for child in ext:
            child_tag = _local_name(child.tag)
            if child_tag == "hr" and child.text:
                hr_values.append(int(child.text))
            elif child_tag == "cad" and child.text:
                cadence_values.append(int(child.text))
            elif _is_rr_tag(child_tag) and child.text:
                _append_rr_interval(rr_intervals, child.text)
    elif tag == "power" and ext.text:
        power_values.append(int(ext.text))
    elif tag == "hr" and ext.text:
        hr_values.append(int(ext.text))
    elif _is_rr_tag(tag) and ext.text:
        _append_rr_interval(rr_intervals, ext.text)

    for child in ext:
        if _local_name(child.tag) != "TrackPointExtension":
            _extract_gpx_extension(child, hr_values, power_values, cadence_values, rr_intervals)


def parse_fit(file_path: str | Path) -> ParsedActivity:  # noqa: C901, PLR0912
    """Parse a Garmin .FIT file into structured activity data."""
    from fitparse import FitFile

    fit = FitFile(str(file_path))

    sport = "general"
    start_time: datetime | None = None
    duration: int | None = None
    distance: float | None = None
    elevation_gain: float | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    avg_power: int | None = None
    avg_cadence: int | None = None
    power_stream: list[int] = []
    rr_intervals: list[int] = []

    for record in fit.get_messages("session"):
        for field in record.fields:
            name = field.name
            val = field.value
            if val is None:
                continue
            if name == "sport":
                sport = str(val).lower()
            elif name == "start_time":
                start_time = val
            elif name == "total_elapsed_time":
                duration = int(val)
            elif name == "total_distance":
                distance = float(val)
            elif name == "total_ascent":
                elevation_gain = float(val)
            elif name == "avg_heart_rate":
                avg_hr = int(val)
            elif name == "max_heart_rate":
                max_hr = int(val)
            elif name == "avg_power":
                avg_power = int(val)
            elif name == "avg_cadence":
                avg_cadence = int(val)

    # Collect power stream from records for NP calculation
    for record in fit.get_messages("record"):
        power_stream.extend(
            int(field.value)
            for field in record.fields
            if field.name == "power" and field.value is not None
        )

    for record in fit.get_messages("hrv"):
        for field in record.fields:
            if field.name != "time" or field.value is None:
                continue
            values = field.value if isinstance(field.value, list) else [field.value]
            for value in values:
                _append_rr_interval(rr_intervals, value)

    activity_date = start_time.date() if start_time else date.today()

    return ParsedActivity(
        sport=sport,
        activity_date=activity_date,
        started_at=start_time,
        duration_seconds=duration,
        distance_meters=distance,
        elevation_gain_meters=elevation_gain,
        avg_hr_bpm=avg_hr,
        max_hr_bpm=max_hr,
        avg_power_watts=avg_power,
        avg_cadence_rpm=avg_cadence,
        power_stream=power_stream if power_stream else None,
        rr_intervals_ms=rr_intervals if rr_intervals else None,
        hrv_summary=summarize_hrv(rr_intervals) if rr_intervals else None,
    )


def parse_tcx(file_path: str | Path) -> ParsedActivity:  # noqa: C901
    """Parse a Garmin TCX file into structured activity data."""
    root = ET.parse(file_path).getroot()
    activity = next(
        (element for element in root.iter() if _local_name(element.tag) == "Activity"),
        None,
    )
    if activity is None:
        raise ValueError("TCX file does not contain an Activity.")

    sport = _normalize_tcx_sport(activity.attrib.get("Sport"))
    start_time = _parse_datetime(_first_text(activity, "Id"))
    duration: int | None = None
    distance: float | None = None
    hr_values: list[int] = []
    rr_intervals: list[int] = []

    total_duration = 0.0
    max_distance = 0.0
    for element in activity.iter():
        tag = _local_name(element.tag)
        if tag == "TotalTimeSeconds" and element.text:
            total_duration += float(element.text)
        elif tag == "DistanceMeters" and element.text:
            max_distance = max(max_distance, float(element.text))
        elif tag == "Time" and start_time is None:
            start_time = _parse_datetime(element.text)
        elif tag == "HeartRateBpm":
            value = _first_text(element, "Value")
            if value:
                hr_values.append(int(value))
        elif _is_rr_tag(tag) and element.text:
            _append_rr_interval(rr_intervals, element.text)

    if total_duration > 0:
        duration = round(total_duration)
    if max_distance > 0:
        distance = max_distance

    activity_date = start_time.date() if start_time else date.today()
    return ParsedActivity(
        sport=sport,
        activity_date=activity_date,
        started_at=start_time,
        duration_seconds=duration,
        distance_meters=distance,
        avg_hr_bpm=round(sum(hr_values) / len(hr_values)) if hr_values else None,
        max_hr_bpm=max(hr_values) if hr_values else None,
        rr_intervals_ms=rr_intervals if rr_intervals else None,
        hrv_summary=summarize_hrv(rr_intervals) if rr_intervals else None,
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _is_rr_tag(tag: str) -> bool:
    return tag.lower() in {"rr", "rri", "rrinterval", "rr_interval"}


def _append_rr_interval(rr_intervals: list[int], value: int | float | str) -> None:
    try:
        interval = float(value)
    except (TypeError, ValueError):
        return

    if interval < SECONDS_TO_MS_THRESHOLD:
        interval *= 1000
    if MIN_RR_INTERVAL_MS <= interval <= MAX_RR_INTERVAL_MS:
        rr_intervals.append(round(interval))


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _first_text(element: ET.Element, tag_name: str) -> str | None:
    for child in element.iter():
        if _local_name(child.tag) == tag_name and child.text:
            return child.text
    return None


def _normalize_tcx_sport(sport: str | None) -> str:
    if not sport:
        return "general"
    normalized = sport.lower()
    if normalized in {"biking", "cycling"}:
        return "cycling"
    if normalized in {"snowboarding", "downhillskiing"}:
        return "downhillskiing"
    return normalized
