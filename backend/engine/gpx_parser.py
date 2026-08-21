"""GPX, FIT, and TCX file parsing to structured activity data."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.engine.course_profile import (
    CoursePoint,
    CourseProfile,
    build_course_points,
    summarize_course,
    summarize_course_with_totals,
)
from backend.engine.hrv import HRVSummary, summarize_hrv

CYCLING_INFERRED_PACE_SEC_KM = 180
MIN_RR_INTERVAL_MS = 300
MAX_RR_INTERVAL_MS = 2000
SECONDS_TO_MS_THRESHOLD = 10

# What we call a sport we cannot determine. Already the fallback used by
# `_FitSessionSummary` and `_normalize_tcx_sport`, and a permitted value in the
# `sport_thresholds` check constraint — so nothing downstream needs to learn it.
UNKNOWN_SPORT = "general"

# Declared-sport vocabulary. Route files name their sport in wildly different
# dialects (`<type>` in GPX, `sport` on a FIT course message), and the value is
# free text, so map only what we recognise onto the canonical set and let anything
# else fall through to inference. Keys are alphanumeric-only; see `_canonical_sport`.
_SPORT_ALIASES: Mapping[str, str] = {
    "bike": "cycling",
    "biking": "cycling",
    "cycle": "cycling",
    "cycling": "cycling",
    "ebikeride": "cycling",
    "gravelride": "cycling",
    "mountainbiking": "cycling",
    "mtb": "cycling",
    "ride": "cycling",
    "roadcycling": "cycling",
    "virtualride": "cycling",
    "hike": "hiking",
    "hiking": "hiking",
    "openwaterswim": "swimming",
    "swim": "swimming",
    "swimming": "swimming",
    "row": "rowing",
    "rowing": "rowing",
    "run": "running",
    "running": "running",
    "trailrun": "running",
    "trailrunning": "running",
    "virtualrun": "running",
}


def _canonical_sport(value: object) -> str | None:
    """Map a declared sport onto the canonical set, or ``None`` if unrecognised.

    Casefolds and drops non-alphanumerics, so ``Trail_Run``, ``trail run``, and
    ``TrailRun`` all collapse to the same key. Returning ``None`` rather than
    guessing matters: Strava writes a numeric ``<type>1</type>``, which must fall
    through to inference instead of poisoning the result.
    """
    if value is None:
        return None
    normalized = "".join(char for char in str(value).casefold() if char.isalnum())
    return _SPORT_ALIASES.get(normalized)


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
    utc_offset_seconds: int | None = None


@dataclass
class ParsedCourse:
    """A planned route, not something the athlete has done.

    Course files carry no time signal, so there is no duration, no heart rate, and
    nothing to score. They exist so the coach can advise on pacing and preparation —
    they must never be persisted as a completed activity.
    """

    sport: str
    profile: CourseProfile
    name: str | None = None


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
    # Declared `<type>` from a <trk> or <rte>, already canonicalised.
    declared_sport: str | None = None
    name: str | None = None
    # Counted so route fallback can tell "no track" from "a track with one point";
    # `total_distance == 0` cannot, and would double-count a file carrying both.
    point_count: int = 0
    # (distance_from_previous_point, elevation) per point, for course grade math.
    segments: list[tuple[float, float | None]] = dataclass_field(default_factory=list)

    @property
    def duration(self) -> int | None:
        if self.start_time is None or self.end_time is None:
            return None
        return int((self.end_time - self.start_time).total_seconds())

    @property
    def is_course(self) -> bool:
        """A GPX that describes no elapsed time is a route, not a recording.

        GPX has no course/activity marker of its own, so time is the only evidence
        the format offers — but the test has to be *elapsed* time, not the mere
        presence of a ``<time>`` element. Planning tools write synthetic timestamps:
        one on the first point, or the same instant on every point. Testing for
        absence-of-any-timestamp let those through as activities with a fabricated
        sport, no duration, and a real chance of satisfying a planned workout.

        A recording always spans a positive interval. A course never does, however
        many timestamps someone stamped on it. An empty or waypoint-only file has no
        interval either, and reporting it as a zero-distance course beats writing a
        phantom workout dated today.

        Strictly positive, so out-of-order timestamps cannot slip through: a file
        whose last point predates its first produced a negative duration, which is
        truthy, so it was saved as an activity lasting minus five minutes.
        """
        duration = self.duration
        return duration is None or duration <= 0

    @property
    def sport(self) -> str:
        if self.declared_sport:
            return self.declared_sport
        duration = self.duration
        if duration is not None and duration > 0 and self.total_distance > 0:
            pace_sec_km = duration / (self.total_distance / 1000)
            if pace_sec_km < CYCLING_INFERRED_PACE_SEC_KM:
                return "cycling"
            return "running"
        # Pace inference needs time, and a course has none. Saying "running" here —
        # as this did — labelled every uploaded ride a run.
        return UNKNOWN_SPORT


def parse_gpx(file_path: str | Path) -> ParsedActivity | ParsedCourse:
    """Parse a GPX file into a recorded activity, or a course if it spans no elapsed time."""
    import gpxpy

    with Path(file_path).open() as f:
        gpx = gpxpy.parse(f)

    summary = _extract_gpx_summary(gpx)
    if summary.is_course:
        return ParsedCourse(
            sport=summary.sport,
            profile=summarize_course(build_course_points(summary.segments)),
            name=summary.name,
        )

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
        _absorb_gpx_container_metadata(summary, track)
        for segment in track.segments:
            _accumulate_gpx_segment(summary, segment.points)

    # <rte>/<rtept> is how Garmin Course and RideWithGPS exports commonly describe a
    # route, and walking only <trk> reported those as zero distance and zero
    # vertical. Fall back rather than add: a Garmin course can carry a track *and* a
    # redundant route, and summing both would double every number.
    # Decide once, before the loop. Re-reading `point_count` inside it made the first
    # route disable every route after it, so a course split across several <rte>
    # elements — which is how RideWithGPS and Garmin often export one — reported only
    # its first leg.
    has_track_points = summary.point_count > 0
    for route in gpx.routes:
        _absorb_gpx_container_metadata(summary, route)
        if not has_track_points:
            _accumulate_gpx_segment(summary, route.points)

    return summary


def _absorb_gpx_container_metadata(summary: _GpxSummary, container: Any) -> None:
    """Take the declared sport and name off a <trk> or <rte>, first one wins.

    Read from routes even when their points are skipped: a track with no <type>
    sitting beside a route that has one should still resolve.
    """
    if summary.declared_sport is None:
        summary.declared_sport = _canonical_sport(getattr(container, "type", None))
    if summary.name is None:
        name = getattr(container, "name", None)
        summary.name = str(name) if name else None


def _accumulate_gpx_segment(summary: _GpxSummary, points: list[Any]) -> None:
    if not points:
        return

    # First and last point *carrying a time*, not strictly the first and last point.
    # A unit that drops the timestamp on its opening fix left start_time unset, which
    # makes the elapsed span None — so a genuine recording was classified as a course
    # and never logged at all. Scanning past the gap costs nothing and closes it.
    if summary.start_time is None:
        summary.start_time = next((point.time for point in points if point.time), None)
    last_timed = next((point.time for point in reversed(points) if point.time), None)
    if last_timed:
        summary.end_time = last_timed

    summary.point_count += len(points)

    for index, point in enumerate(points):
        segment_distance = 0.0
        if index > 0:
            segment_distance = _accumulate_gpx_point_distance(summary, point, points[index - 1])
        summary.segments.append((segment_distance, point.elevation))
        for ext in point.extensions or []:
            _extract_gpx_extension(
                ext,
                summary.hr_values,
                summary.power_values,
                summary.cadence_values,
                summary.rr_intervals,
            )


def _accumulate_gpx_point_distance(summary: _GpxSummary, point: Any, previous_point: Any) -> float:
    """Add one point-to-point step to the running totals, returning its distance."""
    step = point.distance_2d(previous_point) or 0
    summary.total_distance += step
    # Both elevations have to be present. Coalescing a missing one to 0 turned the
    # first reading in a partial stream into a climb from sea level: a track whose
    # opening point has no <ele> followed by one at 500 m reported 500 m of ascent.
    if point.elevation is not None and previous_point.elevation is not None:
        ele_diff = point.elevation - previous_point.elevation
        if ele_diff > 0:
            summary.total_elevation_gain += ele_diff
    return step


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
        return

    _extract_gpx_simple_extension(tag, ext.text, hr_values, power_values, rr_intervals)

    for child in ext:
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
        if isinstance(value, datetime) and (self.start_time is None or value < self.start_time):
            self.start_time = value

    def add_elapsed_time(self, value: Any) -> None:
        self.elapsed_total += float(value)
        self.have_elapsed = True

    def add_timer_time(self, value: Any) -> None:
        self.timer_total += float(value)
        self.have_timer = True

    def add_distance(self, value: Any) -> None:
        self.distance = (self.distance or 0.0) + float(value)

    def add_elevation_gain(self, value: Any) -> None:
        self.elevation_gain = (self.elevation_gain or 0.0) + float(value)

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
    "total_distance": _FitSessionSummary.add_distance,
    "total_ascent": _FitSessionSummary.add_elevation_gain,
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


_MIN_FIT_UTC_OFFSET_SECONDS = -12 * 60 * 60
_MAX_FIT_UTC_OFFSET_SECONDS = 14 * 60 * 60


def _as_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _extract_fit_utc_offset_seconds(fit: Any) -> int | None:
    for message in fit.get_messages("activity"):
        timestamps = {
            field.name: field.value
            for field in message.fields
            if field.name in {"local_timestamp", "timestamp"}
        }
        timestamp = timestamps.get("timestamp")
        local_timestamp = timestamps.get("local_timestamp")
        if not isinstance(timestamp, datetime) or not isinstance(local_timestamp, datetime):
            continue
        offset_minutes = round(
            (_as_naive_utc(local_timestamp) - _as_naive_utc(timestamp)).total_seconds() / 60
        )
        offset_seconds = offset_minutes * 60
        if _MIN_FIT_UTC_OFFSET_SECONDS <= offset_seconds <= _MAX_FIT_UTC_OFFSET_SECONDS:
            return offset_seconds
    return None


def _fit_field(message: Any, name: str) -> Any:
    for field in message.fields:
        if field.name == name and field.value is not None:
            return field.value
    return None


def _extract_fit_course(fit: Any) -> ParsedCourse | None:
    """Build a course from a FIT file's ``course`` message, or ``None`` if it has none.

    Unlike GPX, FIT says outright which it is: a course file carries a ``course``
    message (global message 31) and no ``session``. Its timestamps, where present,
    are synthetic, so absence-of-time is the wrong test here.
    """
    if any(True for _ in fit.get_messages("session")):
        # A recording that happens to embed the course it followed is still a
        # recording.
        return None

    course_messages = list(fit.get_messages("course"))
    # `file_id.type` is the file's own declaration of what it is and is the stronger
    # signal; the `course` message corroborates it. Keying on the message alone meant
    # a course file that omitted it was persisted as an activity dated today.
    declares_course = any(
        str(_fit_field(message, "type") or "").lower() == "course"
        for message in fit.get_messages("file_id")
    )
    if not course_messages and not declares_course:
        return None

    course = course_messages[0] if course_messages else None
    name = _fit_field(course, "name") if course else None
    sport = (_canonical_sport(_fit_field(course, "sport")) if course else None) or UNKNOWN_SPORT

    # `lap` carries the authoritative totals. `record` is walked only for grades,
    # and a course without a usable record stream simply reports grades as None.
    distance: float | None = None
    elevation_gain: float | None = None
    for lap in fit.get_messages("lap"):
        lap_distance = _fit_field(lap, "total_distance")
        if lap_distance is not None:
            distance = (distance or 0.0) + float(lap_distance)
        lap_ascent = _fit_field(lap, "total_ascent")
        if lap_ascent is not None:
            elevation_gain = (elevation_gain or 0.0) + float(lap_ascent)

    return ParsedCourse(
        sport=sport,
        profile=summarize_course_with_totals(
            _fit_course_points(fit),
            distance_meters=distance,
            elevation_gain_meters=elevation_gain,
        ),
        name=str(name) if name else None,
    )


def _fit_course_points(fit: Any) -> list[CoursePoint]:
    """Course points from the record stream: cumulative distance plus altitude."""
    segments: list[tuple[float, float | None]] = []
    # None until the first record with a distance, which anchors the scale rather
    # than travelling. Starting at 0.0 charged the first record's whole cumulative
    # reading as a step, so a stream that opens part-way along the route invented
    # that much distance — the GPX and TCX paths both start their first point at zero.
    previous_distance: float | None = None
    for record in fit.get_messages("record"):
        distance = _fit_field(record, "distance")
        if distance is None:
            continue
        altitude = _fit_field(record, "enhanced_altitude")
        if altitude is None:
            altitude = _fit_field(record, "altitude")
        cumulative = float(distance)
        step = max(0.0, cumulative - previous_distance) if previous_distance is not None else 0.0
        segments.append((step, float(altitude) if altitude is not None else None))
        previous_distance = cumulative
    return build_course_points(segments)


def _extract_fit_power_stream(fit: Any) -> list[int]:
    """Per-record power, for normalized-power calculation."""
    power_stream: list[int] = []
    for record in fit.get_messages("record"):
        power_stream.extend(
            int(field.value)
            for field in record.fields
            if field.name == "power" and field.value is not None
        )
    return power_stream


def _extract_fit_rr_intervals(fit: Any) -> list[int]:
    rr_intervals: list[int] = []
    for record in fit.get_messages("hrv"):
        for field in record.fields:
            if field.name != "time" or field.value is None:
                continue
            values = field.value if isinstance(field.value, list) else [field.value]
            for value in values:
                _append_rr_interval(rr_intervals, value)
    return rr_intervals


def parse_fit(file_path: str | Path) -> ParsedActivity | ParsedCourse:
    """Parse a Garmin .FIT file into a recorded activity, or a course if it is one."""
    from fitparse import FitFile

    fit = FitFile(str(file_path))

    course = _extract_fit_course(fit)
    if course is not None:
        return course

    session_summary = _extract_fit_session_summary(fit)
    utc_offset_seconds = _extract_fit_utc_offset_seconds(fit)
    power_stream = _extract_fit_power_stream(fit)
    rr_intervals = _extract_fit_rr_intervals(fit)

    elapsed_duration_seconds = (
        int(session_summary.elapsed_total) if session_summary.have_elapsed else None
    )
    moving_duration_seconds = (
        int(session_summary.timer_total) if session_summary.have_timer else None
    )
    duration = (
        moving_duration_seconds if moving_duration_seconds is not None else elapsed_duration_seconds
    )

    activity_date = datetime.now(UTC).date()
    if session_summary.start_time is not None:
        local_start_time = _as_naive_utc(session_summary.start_time)
        if utc_offset_seconds is not None:
            local_start_time += timedelta(seconds=utc_offset_seconds)
        activity_date = local_start_time.date()

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
        utc_offset_seconds=utc_offset_seconds,
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


def parse_tcx(file_path: str | Path) -> ParsedActivity | ParsedCourse:
    """Parse a Garmin TCX file into a recorded activity, or a course if it is one.

    A TCX declares which it is structurally — ``<Activities>`` versus ``<Courses>`` —
    so, unlike GPX, timestamps are not the test. Course trackpoints legitimately
    carry (synthetic) ``<Time>`` elements.

    ``Activity`` is checked first so that today's behaviour is preserved exactly for
    every file that already parses, including a hybrid carrying both sections. Only
    the case that used to raise changes.
    """
    root = ET.parse(file_path).getroot()
    activity = next(
        (element for element in root.iter() if _local_name(element.tag) == "Activity"),
        None,
    )
    if activity is None:
        course = next(
            (element for element in root.iter() if _local_name(element.tag) == "Course"),
            None,
        )
        if course is not None:
            return _parse_tcx_course(course)
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


def _tcx_trackpoint_position(trackpoint: ET.Element) -> tuple[float, float] | None:
    latitude = _first_text(trackpoint, "LatitudeDegrees")
    longitude = _first_text(trackpoint, "LongitudeDegrees")
    if not latitude or not longitude:
        return None
    return float(latitude), float(longitude)


def _tcx_lap_distance(course: ET.Element) -> float | None:
    """Total DistanceMeters declared on the Course's own ``<Lap>`` elements.

    Direct children only, for the same reason as ``_tcx_course_name``: a Lap can
    nest its ``<Track>``, and a descendant search then returns the first
    *trackpoint's* reading as the lap total — a marathon course came back as 7 m.
    """
    total: float | None = None
    for lap in course.iter():
        if _local_name(lap.tag) != "Lap":
            continue
        for child in lap:
            if _local_name(child.tag) == "DistanceMeters" and child.text:
                total = (total or 0.0) + float(child.text)
                break
    return total


def _tcx_course_name(course: ET.Element) -> str | None:
    """The Course's own ``<Name>``, never a ``<CoursePoint>`` waypoint label.

    ``_first_text`` walks descendants, so a course with no name of its own but with
    course points took the first waypoint's name — the coach then told the athlete
    their route was called "Water stop".
    """
    for child in course:
        if _local_name(child.tag) == "Name" and child.text:
            return child.text.strip() or None
    return None


def _parse_tcx_course(course: ET.Element) -> ParsedCourse:
    """Build a course from a TCX ``<Course>`` element.

    The Course schema has no sport attribute, so sport stays unknown and the coach
    asks. Distance prefers the trackpoints' own cumulative ``DistanceMeters``, which
    is what Garmin writes and is authoritative when present.

    ``DistanceMeters`` is optional on a Trackpoint, though. Falling back to the
    previous cumulative value made every delta zero, so a file without it reported a
    coherent-looking "0 m course with 800 m of climbing" — the totals silently wrong
    while the elevation was right. Where the trackpoint carries a position instead,
    the distance is derived from coordinates, the same measurement the GPX path uses.
    """
    from gpxpy.geo import haversine_distance

    segments: list[tuple[float, float | None]] = []
    traveled = 0.0
    previous_position: tuple[float, float] | None = None

    for trackpoint in course.iter():
        if _local_name(trackpoint.tag) != "Trackpoint":
            continue
        altitude_text = _first_text(trackpoint, "AltitudeMeters")
        elevation = float(altitude_text) if altitude_text else None
        distance_text = _first_text(trackpoint, "DistanceMeters")
        position = _tcx_trackpoint_position(trackpoint)

        # One running total, advanced by every step whatever its source. A declared
        # DistanceMeters is cumulative, so its step is the gap up to that total. The
        # clamp makes it one-sided on purpose: a declared reading can pull the total
        # forward, but never drags it back below distance already measured from
        # positions. Keeping a separate declared-only baseline got both orders wrong —
        # a course declaring 178 m came out at 267 m when the declared reading came
        # first, and at 89 m when it came last.
        if distance_text:
            declared = float(distance_text)
            if segments:
                step = max(0.0, declared - traveled)
            else:
                # The file's very first trackpoint. A course exported part-way along
                # its route opens at a non-zero cumulative reading, which is where the
                # route starts rather than distance the athlete covers — charging it
                # as a step turned a 1 km course opening at 5 km into a 6 km one.
                # Anchoring only here, rather than on the first *declared* reading,
                # keeps a declared value that arrives after position-derived movement
                # working as the cumulative total it is.
                traveled = declared
                step = 0.0
        elif position is not None and previous_position is not None:
            step = haversine_distance(*previous_position, *position) or 0.0
        else:
            step = 0.0
        traveled += step

        if position is not None:
            previous_position = position
        segments.append((step, elevation))

    # A Course whose trackpoints carry neither DistanceMeters nor Position leaves the
    # stream at zero. The Lap's own total is the only distance the file states, so use
    # it rather than reporting a coherent-looking "0 m course with 800 m of climbing".
    # Scoped to the Lap element: `_first_text` over the whole Course would happily
    # return a trackpoint's reading instead. Trackpoint-derived distance stays
    # authoritative whenever there is any.
    lap_distance = _tcx_lap_distance(course)
    points = build_course_points(segments)
    profile = summarize_course(points)
    if profile.distance_meters <= 0 and lap_distance:
        profile = summarize_course_with_totals(
            points, distance_meters=lap_distance, elevation_gain_meters=None
        )

    return ParsedCourse(
        sport=UNKNOWN_SPORT,
        profile=profile,
        name=_tcx_course_name(course),
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
