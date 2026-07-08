"""GPX, FIT, and TCX file parsing to structured activity data."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
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
    elapsed_duration_seconds: int | None = None
    moving_duration_seconds: int | None = None
    distance_meters: float | None = None
    elevation_gain_meters: float | None = None
    avg_hr_bpm: int | None = None
    max_hr_bpm: int | None = None
    avg_power_watts: int | None = None
    avg_cadence_rpm: int | None = None
    power_stream: list[int] | None = None  # for NP calculation
    rr_intervals_ms: list[int] | None = None
    hrv_summary: HRVSummary | None = None


@dataclass
class _GpxSummary:
    total_distance: float = 0.0
    total_elevation_gain: float = 0.0
    hr_values: list[int] = dataclass_field(default_factory=list)
    power_values: list[int] = dataclass_field(default_factory=list)
    cadence_values: list[int] = dataclass_field(default_factory=list)
    rr_intervals: list[int] = dataclass_field(default_factory=list)
    start_time: datetime | None = None
    end_time: datetime | None = None

    @property
    def duration(self) -> int | None:
        if self.start_time is None or self.end_time is None:
            return None
        return int((self.end_time - self.start_time).total_seconds())

    @property
    def sport(self) -> str:
        if self.duration and self.total_distance > 0:
            pace_sec_km = self.duration / (self.total_distance / 1000)
            if pace_sec_km < CYCLING_INFERRED_PACE_SEC_KM:
                return "cycling"
        return "running"


def parse_gpx(file_path: str | Path) -> ParsedActivity:
    """Parse a GPX file into structured activity data."""
    import gpxpy

    with Path(file_path).open() as f:
        gpx = gpxpy.parse(f)

    summary = _extract_gpx_summary(gpx)
    duration = summary.duration
    activity_date = summary.start_time.date() if summary.start_time else date.today()
    hr_values = summary.hr_values
    power_values = summary.power_values
    cadence_values = summary.cadence_values
    rr_intervals = summary.rr_intervals

    return ParsedActivity(
        sport=summary.sport,
        activity_date=activity_date,
        started_at=summary.start_time,
        duration_seconds=duration,
        # GPX's only duration signal is the timestamp span between first/last
        # point, i.e. wall-clock time including any gaps — treat it as elapsed,
        # not moving time.
        elapsed_duration_seconds=duration,
        distance_meters=round(summary.total_distance, 1) if summary.total_distance > 0 else None,
        elevation_gain_meters=round(summary.total_elevation_gain, 1)
        if summary.total_elevation_gain > 0
        else None,
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


def _extract_gpx_summary(gpx: Any) -> _GpxSummary:
    summary = _GpxSummary()
    for track in gpx.tracks:
        for segment in track.segments:
            _accumulate_gpx_segment(summary, segment.points)
    return summary


def _accumulate_gpx_segment(summary: _GpxSummary, points: list[Any]) -> None:
    if not points:
        return

    if summary.start_time is None and points[0].time:
        summary.start_time = points[0].time
    if points[-1].time:
        summary.end_time = points[-1].time

    for index, point in enumerate(points):
        if index > 0:
            _accumulate_gpx_point_distance(summary, point, points[index - 1])
        for ext in point.extensions or []:
            _extract_gpx_extension(
                ext,
                summary.hr_values,
                summary.power_values,
                summary.cadence_values,
                summary.rr_intervals,
            )


def _accumulate_gpx_point_distance(summary: _GpxSummary, point: Any, previous_point: Any) -> None:
    summary.total_distance += point.distance_2d(previous_point) or 0
    ele_diff = (point.elevation or 0) - (previous_point.elevation or 0)
    if ele_diff > 0:
        summary.total_elevation_gain += ele_diff


def _extract_gpx_extension(
    ext: Any,
    hr_values: list[int],
    power_values: list[int],
    cadence_values: list[int],
    rr_intervals: list[int],
) -> None:
    """Extract HR, power, cadence, and RR intervals from GPX extension elements."""
    tag = _local_name(ext.tag)

    if tag == "TrackPointExtension":
        _extract_gpx_trackpoint_extension(ext, hr_values, cadence_values, rr_intervals)
    else:
        _extract_gpx_simple_extension(tag, ext.text, hr_values, power_values, rr_intervals)

    for child in ext:
        if _local_name(child.tag) != "TrackPointExtension":
            _extract_gpx_extension(child, hr_values, power_values, cadence_values, rr_intervals)


def _extract_gpx_trackpoint_extension(
    ext: Any,
    hr_values: list[int],
    cadence_values: list[int],
    rr_intervals: list[int],
) -> None:
    for child in ext:
        child_tag = _local_name(child.tag)
        if child_tag == "hr" and child.text:
            hr_values.append(int(child.text))
        elif child_tag == "cad" and child.text:
            cadence_values.append(int(child.text))
        elif _is_rr_tag(child_tag) and child.text:
            _append_rr_interval(rr_intervals, child.text)


def _extract_gpx_simple_extension(
    tag: str,
    text: str | None,
    hr_values: list[int],
    power_values: list[int],
    rr_intervals: list[int],
) -> None:
    if tag == "power" and text:
        power_values.append(int(text))
    elif tag == "hr" and text:
        hr_values.append(int(text))
    elif _is_rr_tag(tag) and text:
        _append_rr_interval(rr_intervals, text)


@dataclass
class _FitSessionSummary:
    sport: str = "general"
    start_time: datetime | None = None
    elapsed_total: float = 0.0
    timer_total: float = 0.0
    have_elapsed: bool = False
    have_timer: bool = False
    distance: float | None = None
    elevation_gain: float | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    avg_power: int | None = None
    avg_cadence: int | None = None

    def set_sport(self, value: Any) -> None:
        self.sport = str(value).lower()

    def set_start_time(self, value: Any) -> None:
        if isinstance(value, datetime):
            self.start_time = value

    def add_elapsed_time(self, value: Any) -> None:
        self.elapsed_total += float(value)
        self.have_elapsed = True

    def add_timer_time(self, value: Any) -> None:
        self.timer_total += float(value)
        self.have_timer = True

    def set_distance(self, value: Any) -> None:
        self.distance = float(value)

    def set_elevation_gain(self, value: Any) -> None:
        self.elevation_gain = float(value)

    def set_avg_hr(self, value: Any) -> None:
        self.avg_hr = int(value)

    def set_max_hr(self, value: Any) -> None:
        self.max_hr = int(value)

    def set_avg_power(self, value: Any) -> None:
        self.avg_power = int(value)

    def set_avg_cadence(self, value: Any) -> None:
        self.avg_cadence = int(value)


_FitFieldApplier = Callable[[_FitSessionSummary, Any], None]

_FIT_SESSION_FIELD_APPLIERS: dict[str, _FitFieldApplier] = {
    "sport": _FitSessionSummary.set_sport,
    "start_time": _FitSessionSummary.set_start_time,
    "total_elapsed_time": _FitSessionSummary.add_elapsed_time,
    "total_timer_time": _FitSessionSummary.add_timer_time,
    "total_distance": _FitSessionSummary.set_distance,
    "total_ascent": _FitSessionSummary.set_elevation_gain,
    "avg_heart_rate": _FitSessionSummary.set_avg_hr,
    "max_heart_rate": _FitSessionSummary.set_max_hr,
    "avg_power": _FitSessionSummary.set_avg_power,
    "avg_cadence": _FitSessionSummary.set_avg_cadence,
}


def _extract_fit_session_summary(fit: Any) -> _FitSessionSummary:
    summary = _FitSessionSummary()

    # A FIT file can contain multiple `session` messages (multi-sport/"brick"
    # workouts). Sum durations across all of them rather than overwriting, so
    # a trailing short session doesn't silently replace the real total.
    for record in fit.get_messages("session"):
        for field in record.fields:
            if field.value is None:
                continue
            applier = _FIT_SESSION_FIELD_APPLIERS.get(field.name)
            if applier is not None:
                applier(summary, field.value)

    return summary


def parse_fit(file_path: str | Path) -> ParsedActivity:
    """Parse a Garmin .FIT file into structured activity data."""
    from fitparse import FitFile

    fit = FitFile(str(file_path))

    power_stream: list[int] = []
    rr_intervals: list[int] = []
    session_summary = _extract_fit_session_summary(fit)

    elapsed_duration_seconds = (
        int(session_summary.elapsed_total) if session_summary.have_elapsed else None
    )
    moving_duration_seconds = (
        int(session_summary.timer_total) if session_summary.have_timer else None
    )
    duration = moving_duration_seconds or elapsed_duration_seconds

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

    activity_date = (
        session_summary.start_time.date() if session_summary.start_time else date.today()
    )

    return ParsedActivity(
        sport=session_summary.sport,
        activity_date=activity_date,
        started_at=session_summary.start_time,
        duration_seconds=duration,
        elapsed_duration_seconds=elapsed_duration_seconds,
        moving_duration_seconds=moving_duration_seconds,
        distance_meters=session_summary.distance,
        elevation_gain_meters=session_summary.elevation_gain,
        avg_hr_bpm=session_summary.avg_hr,
        max_hr_bpm=session_summary.max_hr,
        avg_power_watts=session_summary.avg_power,
        avg_cadence_rpm=session_summary.avg_cadence,
        power_stream=power_stream if power_stream else None,
        rr_intervals_ms=rr_intervals if rr_intervals else None,
        hrv_summary=summarize_hrv(rr_intervals) if rr_intervals else None,
    )


@dataclass
class _TcxSummary:
    sport: str
    start_time: datetime | None
    total_duration: float = 0.0
    max_distance: float = 0.0
    hr_values: list[int] = dataclass_field(default_factory=list)
    rr_intervals: list[int] = dataclass_field(default_factory=list)

    @property
    def duration(self) -> int | None:
        return round(self.total_duration) if self.total_duration > 0 else None

    @property
    def distance(self) -> float | None:
        return self.max_distance if self.max_distance > 0 else None


def parse_tcx(file_path: str | Path) -> ParsedActivity:
    """Parse a Garmin TCX file into structured activity data."""
    root = ET.parse(file_path).getroot()
    activity = next(
        (element for element in root.iter() if _local_name(element.tag) == "Activity"),
        None,
    )
    if activity is None:
        raise ValueError("TCX file does not contain an Activity.")

    summary = _extract_tcx_summary(activity)
    duration = summary.duration
    hr_values = summary.hr_values
    rr_intervals = summary.rr_intervals

    activity_date = summary.start_time.date() if summary.start_time else date.today()
    return ParsedActivity(
        sport=summary.sport,
        activity_date=activity_date,
        started_at=summary.start_time,
        duration_seconds=duration,
        # TCX's duration is the sum of each Lap's TotalTimeSeconds, which is
        # Garmin's per-lap timer time (excludes auto-pause) — treat it as
        # moving time, not elapsed.
        moving_duration_seconds=duration,
        distance_meters=summary.distance,
        avg_hr_bpm=round(sum(hr_values) / len(hr_values)) if hr_values else None,
        max_hr_bpm=max(hr_values) if hr_values else None,
        rr_intervals_ms=rr_intervals if rr_intervals else None,
        hrv_summary=summarize_hrv(rr_intervals) if rr_intervals else None,
    )


def _extract_tcx_summary(activity: ET.Element) -> _TcxSummary:
    summary = _TcxSummary(
        sport=_normalize_tcx_sport(activity.attrib.get("Sport")),
        start_time=_parse_datetime(_first_text(activity, "Id")),
    )

    for element in activity.iter():
        _accumulate_tcx_element(summary, element)

    return summary


def _accumulate_tcx_element(summary: _TcxSummary, element: ET.Element) -> None:
    tag = _local_name(element.tag)
    if tag == "TotalTimeSeconds" and element.text:
        summary.total_duration += float(element.text)
    elif tag == "DistanceMeters" and element.text:
        summary.max_distance = max(summary.max_distance, float(element.text))
    elif tag == "Time" and summary.start_time is None:
        summary.start_time = _parse_datetime(element.text)
    elif tag == "HeartRateBpm":
        _append_tcx_heart_rate(summary.hr_values, element)
    elif _is_rr_tag(tag) and element.text:
        _append_rr_interval(summary.rr_intervals, element.text)


def _append_tcx_heart_rate(hr_values: list[int], element: ET.Element) -> None:
    value = _first_text(element, "Value")
    if value:
        hr_values.append(int(value))


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
