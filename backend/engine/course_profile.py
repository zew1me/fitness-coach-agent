"""Terrain math for uploaded *course* files (planned routes, not recorded activities).

A course carries no time signal, so the only things it can tell us are geometric:
how far, how much vertical, and how steep. Those four numbers map one-to-one onto
the ``goals.course_*`` columns and are the inputs
``backend/engine/course_analyzer.py`` needs to estimate pacing and training emphasis.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise

# A grade needs two elevations to exist at all.
MIN_POINTS_FOR_GRADE = 2

# Grade over a short span is dominated by GPS/barometric jitter: two points three
# metres apart with a one-metre elevation error read as a 33% wall. Max grade is
# therefore measured over a window of at least this length.
MIN_GRADE_WINDOW_METERS = 50.0

# ...and, where the sampling allows it, no longer than this. Two reasons, one
# conceptual and one practical. Conceptually, "max grade" is a short-pitch metric —
# averaged over a kilometre it stops describing the steepest thing the athlete will
# meet and starts describing the climb as a whole, which `avg_grade_pct` already
# covers. Practically, it bounds the search: without a ceiling, finding the true
# maximum over every window is quadratic in the point count, and a 90 km gravel
# course has a lot of points. This is a stopping rule rather than a hard filter —
# see `_max_windowed_grade` for why a coarsely sampled course still gets an answer.
MAX_GRADE_WINDOW_METERS = 200.0


@dataclass(frozen=True)
class CoursePoint:
    """One sample along a course: distance travelled so far, plus elevation."""

    cumulative_distance_meters: float
    elevation_meters: float | None


@dataclass(frozen=True)
class CourseProfile:
    """Geometric summary of a course.

    ``avg_grade_pct`` is the average grade *of the ascending portions*, not the net
    grade over the whole course. ``analyze_cycling_climb`` inverts it to recover the
    climbing distance, so a net figure would be meaningless there.

    Grades are ``None`` — never a fabricated ``0.0`` — when the source file carries
    no usable elevation stream (a FIT course whose lap totals are present but whose
    record messages are not, for instance).
    """

    distance_meters: float
    elevation_gain_meters: float
    avg_grade_pct: float | None
    max_grade_pct: float | None


def build_course_points(
    segments: Iterable[tuple[float, float | None]],
) -> list[CoursePoint]:
    """Build course points from ``(segment_distance_meters, elevation)`` pairs.

    The caller supplies per-segment distances because each source format measures
    them differently (gpxpy's ``distance_2d`` for GPX/TCX, FIT's cumulative
    ``distance`` field), and this module deliberately owns no geodesy.
    """
    points: list[CoursePoint] = []
    cumulative = 0.0
    for segment_distance, elevation in segments:
        cumulative += max(0.0, segment_distance)
        points.append(CoursePoint(cumulative, elevation))
    return points


def summarize_course(points: Sequence[CoursePoint]) -> CourseProfile:
    """Reduce a course's point stream to distance, vertical, and grade."""
    distance = points[-1].cumulative_distance_meters if points else 0.0
    gain, ascending_distance = _accumulate_ascent(points)

    avg_grade = None
    if _has_elevation(points) and ascending_distance > 0:
        avg_grade = round(gain / ascending_distance * 100, 1)

    return CourseProfile(
        distance_meters=round(distance, 1),
        elevation_gain_meters=round(gain, 1),
        avg_grade_pct=avg_grade,
        max_grade_pct=_max_windowed_grade(points),
    )


def summarize_course_with_totals(
    points: Sequence[CoursePoint],
    *,
    distance_meters: float | None,
    elevation_gain_meters: float | None,
) -> CourseProfile:
    """Summarize a course whose distance/vertical totals come from the file itself.

    FIT courses report authoritative totals on their ``lap`` messages; the point
    stream, when present, is only good for grades. Supplied totals win; anything
    missing falls back to what the points imply.
    """
    derived = summarize_course(points)
    return CourseProfile(
        distance_meters=round(distance_meters, 1)
        if distance_meters is not None
        else derived.distance_meters,
        elevation_gain_meters=round(elevation_gain_meters, 1)
        if elevation_gain_meters is not None
        else derived.elevation_gain_meters,
        avg_grade_pct=derived.avg_grade_pct,
        max_grade_pct=derived.max_grade_pct,
    )


def _has_elevation(points: Sequence[CoursePoint]) -> bool:
    return any(point.elevation_meters is not None for point in points)


def _accumulate_ascent(points: Sequence[CoursePoint]) -> tuple[float, float]:
    """Total positive elevation change and the distance covered while ascending."""
    gain = 0.0
    ascending_distance = 0.0
    for previous, current in pairwise(points):
        if previous.elevation_meters is None or current.elevation_meters is None:
            continue
        span = current.cumulative_distance_meters - previous.cumulative_distance_meters
        # A rise over zero horizontal distance is a vertical teleport: duplicated
        # points with differing elevations, or a backwards jump that
        # `build_course_points` clamped to zero. Counting it inflates the vertical
        # while contributing nothing to the distance it is averaged over.
        if span <= 0:
            continue
        rise = current.elevation_meters - previous.elevation_meters
        if rise <= 0:
            continue
        gain += rise
        ascending_distance += span
    return gain, ascending_distance


def _max_windowed_grade(points: Sequence[CoursePoint]) -> float | None:
    """Steepest grade over any window between the min and max window lengths.

    Every window in that range is considered, not just the shortest one anchored at
    each start. Checking only the shortest is tempting and wrong: with a 50 m
    minimum and points at 0 m/0 m, 50 m/0 m, 60 m/10 m, the shortest window from the
    first point is flat, no window starts at the second, and the real 16.7% pitch
    between 0 m and 60 m goes unreported.

    Both endpoints of a window must carry elevation, so a file with sparse
    elevations still yields a usable answer instead of dropping to ``None``.
    """
    elevated = [point for point in points if point.elevation_meters is not None]
    if len(elevated) < MIN_POINTS_FOR_GRADE:
        return None

    steepest: float | None = None
    # Cumulative distance never decreases, so the first candidate far enough from the
    # origin never moves backwards as the origin advances: one monotonic pointer
    # skips the whole sub-minimum prefix instead of re-walking it. Without that, a
    # file whose points share a location — a duplicated or paused export — spends the
    # entire remaining tail failing the minimum, for every origin, and the scan goes
    # quadratic on exactly the malformed input least worth spending time on.
    window_start = 1
    for start, origin in enumerate(elevated):
        window_start = max(window_start, start + 1)
        while (
            window_start < len(elevated)
            and elevated[window_start].cumulative_distance_meters
            - origin.cumulative_distance_meters
            < MIN_GRADE_WINDOW_METERS
        ):
            window_start += 1
        if window_start >= len(elevated):
            # No window reaches the minimum from here, and later origins are only
            # further along, so none of them will either.
            break

        # Indexed rather than sliced. `elevated[window_start:]` copies the whole
        # remaining tail on every origin, which is quadratic in memory traffic even
        # when the loop below breaks on its first candidate — 50k points clustered at
        # two locations spent 1.3s doing nothing but copying references.
        for index in range(window_start, len(elevated)):
            candidate = elevated[index]
            span = candidate.cumulative_distance_meters - origin.cumulative_distance_meters
            # `elevation_meters` is non-None for every member of `elevated`; the
            # explicit reads keep the type checker honest without a cast.
            rise = (candidate.elevation_meters or 0.0) - (origin.elevation_meters or 0.0)
            grade = rise / span * 100
            if steepest is None or grade > steepest:
                steepest = grade
            # The ceiling stops the scan, but only *after* this window counts. A
            # course sampled every 500 m has no window under the ceiling at all;
            # breaking first would report no max grade rather than a coarse one.
            if span >= MAX_GRADE_WINDOW_METERS:
                break

    return round(steepest, 1) if steepest is not None else None
