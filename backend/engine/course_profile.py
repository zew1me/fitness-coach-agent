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
from math import isfinite

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

# Minimum spacing between the samples the grade scan actually looks at. The ceiling
# above bounds a window by *distance*, but nothing bounds how many points sit inside
# one: 15 000 samples packed into 150 m never reach the ceiling at all, so the scan
# walked the whole tail for every origin and took 3.2s.
#
# One metre is deliberately below GPS precision, so for any real file this thins
# nothing and the scan is exact. It only engages on sub-metre sampling, where the
# extra points cannot describe real terrain anyway — and there it is the difference
# between bounded work and none. A coarser threshold is tempting and costs accuracy:
# at 5 m, points at 0 m, 4.9 m, and 54.9 m lose the exact 50 m window between the
# last two and report 91.1% instead of 100%.
GRADE_SCAN_MIN_SPACING_METERS = 1.0


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
    """
    Build cumulative course points from segment distances and optional elevations.
    
    Parameters:
    	segments (Iterable[tuple[float, float | None]]): Segment distances in meters
    		and optional elevation readings in meters.
    
    Returns:
    	list[CoursePoint]: Course points with cumulative non-negative distances and
    		invalid elevations represented as ``None``.
    """
    points: list[CoursePoint] = []
    cumulative = 0.0
    for segment_distance, elevation in segments:
        # A corrupt file can carry NaN or infinity in either field. Left alone they
        # propagate through every downstream sum into the response, where
        # `json.dumps` writes them bare and produces invalid JSON. A non-finite
        # distance is no movement; a non-finite elevation is no reading.
        step = segment_distance if isfinite(segment_distance) else 0.0
        cumulative += max(0.0, step)
        points.append(
            CoursePoint(
                cumulative,
                elevation if elevation is not None and isfinite(elevation) else None,
            )
        )
    return points


def _finite_points(points: Sequence[CoursePoint]) -> list[CoursePoint]:
    """
    Filter out points with non-finite cumulative distances and normalize invalid elevations to ``None``.
    
    Parameters:
    	points (Sequence[CoursePoint]): Course points to sanitize.
    
    Returns:
    	list[CoursePoint]: Points with finite cumulative distances and normalized elevations.
    """
    return [
        CoursePoint(point.cumulative_distance_meters, _elevation_of(point))
        for point in points
        if isfinite(point.cumulative_distance_meters)
    ]


def _elevation_of(point: CoursePoint) -> float | None:
    """
    Get a point's finite elevation reading.
    
    Returns:
    	float | None: The elevation in meters, or `None` when the reading is missing or non-finite.
    """
    elevation = point.elevation_meters
    return elevation if elevation is not None and isfinite(elevation) else None


def _thinned_for_grade_scan(points: Sequence[CoursePoint]) -> list[CoursePoint]:
    """Reduce closely spaced samples for grade scanning while preserving the final point."""
    kept: list[CoursePoint] = []
    for point in points:
        if (
            not kept
            or point.cumulative_distance_meters - kept[-1].cumulative_distance_meters
            >= GRADE_SCAN_MIN_SPACING_METERS
        ):
            kept.append(point)
    if points and kept[-1] is not points[-1]:
        kept.append(points[-1])
    return kept


def summarize_course(points: Sequence[CoursePoint]) -> CourseProfile:
    """
    Summarize a course's distance, elevation gain, and grade metrics.
    
    Parameters:
        points (Sequence[CoursePoint]): Course points ordered by cumulative distance.
    
    Returns:
        CourseProfile: Rounded distance and elevation metrics, with grade values when sufficient elevation data is available.
    """
    points = _finite_points(points)
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
    """
    Summarize a course using supplied totals when they are valid.
    
    Parameters:
        points (Sequence[CoursePoint]): Course points used to derive fallback totals and grade metrics.
        distance_meters (float | None): Supplied total distance.
        elevation_gain_meters (float | None): Supplied total elevation gain.
    
    Returns:
        CourseProfile: Course summary using valid supplied totals and point-derived grade metrics.
    """
    derived = summarize_course(points)
    return CourseProfile(
        distance_meters=_usable_total(distance_meters, derived.distance_meters),
        elevation_gain_meters=_usable_total(elevation_gain_meters, derived.elevation_gain_meters),
        avg_grade_pct=derived.avg_grade_pct,
        max_grade_pct=derived.max_grade_pct,
    )


def _usable_total(supplied: float | None, fallback: float) -> float:
    """
    Select a supplied total when it is finite and non-negative; otherwise use the fallback.
    
    Parameters:
    	supplied (float | None): The proposed total.
    	fallback (float): The value to use when the supplied total is invalid.
    
    Returns:
    	float: The selected total rounded to one decimal place when supplied, or the fallback value.
    """
    if supplied is None or not isfinite(supplied) or supplied < 0:
        return fallback
    return round(supplied, 1)


def _has_elevation(points: Sequence[CoursePoint]) -> bool:
    """Determine whether any course point has a valid elevation value."""
    return any(_elevation_of(point) is not None for point in points)


def _accumulate_ascent(points: Sequence[CoursePoint]) -> tuple[float, float]:
    """
    Calculate total elevation gain and the distance covered during ascents.
    
    Parameters:
        points (Sequence[CoursePoint]): Course points ordered by cumulative distance.
    
    Returns:
        tuple[float, float]: Total positive elevation gain and ascending distance.
    """
    gain = 0.0
    ascending_distance = 0.0
    for previous, current in pairwise(points):
        previous_elevation = _elevation_of(previous)
        current_elevation = _elevation_of(current)
        if previous_elevation is None or current_elevation is None:
            continue
        span = current.cumulative_distance_meters - previous.cumulative_distance_meters
        # A rise over zero horizontal distance is a vertical teleport: duplicated
        # points with differing elevations, or a backwards jump that
        # `build_course_points` clamped to zero. Counting it inflates the vertical
        # while contributing nothing to the distance it is averaged over.
        if span <= 0:
            continue
        rise = current_elevation - previous_elevation
        if rise <= 0:
            continue
        gain += rise
        ascending_distance += span
    return gain, ascending_distance


def _max_windowed_grade(points: Sequence[CoursePoint]) -> float | None:
    """
    Determine the steepest signed grade across valid distance windows.
    
    Parameters:
        points (Sequence[CoursePoint]): Course points used for grade calculation.
    
    Returns:
        float | None: The steepest grade percentage, rounded to one decimal place,
        or None when fewer than two elevated points or no valid window exists.
    """
    elevated = _thinned_for_grade_scan(
        [point for point in points if _elevation_of(point) is not None]
    )
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
            rise = (_elevation_of(candidate) or 0.0) - (_elevation_of(origin) or 0.0)
            grade = rise / span * 100
            if steepest is None or grade > steepest:
                steepest = grade
            # The ceiling stops the scan, but only *after* this window counts. A
            # course sampled every 500 m has no window under the ceiling at all;
            # breaking first would report no max grade rather than a coarse one.
            if span >= MAX_GRADE_WINDOW_METERS:
                break

    return round(steepest, 1) if steepest is not None else None
