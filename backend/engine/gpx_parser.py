"""GPX and FIT file parsing to structured activity data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


@dataclass
class ParsedActivity:
    """Structured activity data extracted from a GPX or FIT file."""

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


def parse_gpx(file_path: str | Path) -> ParsedActivity:
    """Parse a GPX file into structured activity data."""
    import gpxpy

    with open(file_path) as f:
        gpx = gpxpy.parse(f)

    total_distance = 0.0
    total_elevation_gain = 0.0
    hr_values: list[int] = []
    power_values: list[int] = []
    cadence_values: list[int] = []
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
                        _extract_gpx_extension(ext, hr_values, power_values, cadence_values)

    duration = None
    if start_time and end_time:
        duration = int((end_time - start_time).total_seconds())

    activity_date = start_time.date() if start_time else date.today()

    # Infer sport from average pace if available
    sport = "running"
    if duration and total_distance > 0:
        pace_sec_km = duration / (total_distance / 1000)
        if pace_sec_km < 180:  # faster than 3:00/km → likely cycling
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
        avg_cadence_rpm=round(sum(cadence_values) / len(cadence_values)) if cadence_values else None,
        power_stream=power_values if power_values else None,
    )


def _extract_gpx_extension(
    ext,  # noqa: ANN001 — lxml Element
    hr_values: list[int],
    power_values: list[int],
    cadence_values: list[int],
) -> None:
    """Extract HR, power, cadence from GPX extension elements."""
    tag = ext.tag.split("}")[-1] if "}" in ext.tag else ext.tag

    if tag == "TrackPointExtension":
        for child in ext:
            child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if child_tag == "hr" and child.text:
                hr_values.append(int(child.text))
            elif child_tag == "cad" and child.text:
                cadence_values.append(int(child.text))
    elif tag == "power" and ext.text:
        power_values.append(int(ext.text))
    elif tag == "hr" and ext.text:
        hr_values.append(int(ext.text))


def parse_fit(file_path: str | Path) -> ParsedActivity:
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
        for field in record.fields:
            if field.name == "power" and field.value is not None:
                power_stream.append(int(field.value))

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
    )
