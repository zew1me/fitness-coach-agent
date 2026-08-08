"""Turn a parsed course into something the coach can advise from.

Pairs the course's terrain (``backend/engine/course_profile.py``) with the athlete's
own thresholds so ``backend/engine/course_analyzer.py`` can estimate a finish time,
a VAM target, and where training should be pointed.

Everything here degrades rather than fails. An athlete who has not given us an FTP
yet should get the terrain plus a plain reason the estimate is missing — that is the
coach's cue to ask — never a 5xx that loses the upload.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from backend.engine.course_analyzer import (
    HIGH_ALTITUDE_GAIN_METERS,
    CourseAnalysis,
    analyze_cycling_climb,
    analyze_mountain_objective,
    analyze_running_climb,
)
from backend.engine.gpx_parser import ParsedCourse
from backend.models.athlete import AthleteProfile, SportThreshold


@dataclass(frozen=True)
class AthleteCourseContext:
    """What we know about the athlete when analyzing a course.

    ``lookup_failed`` is the part that is easy to leave out and expensive to omit:
    "this athlete has no FTP" and "we could not read this athlete's data" both
    arrive as absent values, and only the first should produce a message asking
    them to supply one.
    """

    profile: AthleteProfile | None = None
    thresholds: list[SportThreshold] = field(default_factory=list)
    lookup_failed: bool = False


def build_course_payload(
    course: ParsedCourse,
    *,
    athlete: AthleteCourseContext,
    source_file_key: str | None = None,
    public_url: str | None = None,
) -> dict[str, Any]:
    """Assemble the tool result for an uploaded course.

    ``kind`` is the discriminator the coach reads to know nothing was logged. It
    matches the shape already used for activity entries in the zip response.
    """
    analysis, unavailable_reason = analyze_course(course, athlete=athlete)

    return {
        "kind": "course",
        "status": "analyzed",
        "course": {
            "name": course.name,
            "sport": course.sport,
            "distance_meters": course.profile.distance_meters,
            "elevation_gain_meters": course.profile.elevation_gain_meters,
            "avg_grade_pct": course.profile.avg_grade_pct,
            "max_grade_pct": course.profile.max_grade_pct,
            "source_file_key": source_file_key,
            "public_url": public_url,
        },
        "analysis": _analysis_to_dict(analysis) if analysis is not None else None,
        "analysis_unavailable_reason": unavailable_reason,
    }


LOOKUP_FAILED_REASON = (
    "Your profile and thresholds couldn't be read just now, so there's no pacing "
    "estimate for this one. The terrain below is accurate. Try again shortly."
)

# Only the cycling model needs this. An unknown grade is missing data, not a zero:
# substituting 0.0 looks harmless because `analyze_cycling_climb` guards its
# climb-distance division, but the guard is what does the damage — at zero grade it
# treats the whole route as flat and adds a full 30 km/h transit *on top of* the climb
# time. A 30 km course with 1200 m of vertical came back at 2.14 h instead of 1.64 h,
# reported as authoritative.
UNKNOWN_CLIMB_GRADE_REASON = (
    "This file has distance and vertical but no usable elevation stream, so the "
    "climbing grade is unknown and a ride estimate would be wrong. The terrain "
    "totals are still accurate — use those."
)


def analyze_course(
    course: ParsedCourse,
    *,
    athlete: AthleteCourseContext,
) -> tuple[CourseAnalysis | None, str | None]:
    """Pick and run the analyzer that fits this course, or explain why we can't.

    Returns ``(analysis, reason)`` with exactly one of them set.

    ``athlete.lookup_failed`` distinguishes "this athlete has no FTP" from "we could
    not read the athlete's data". Both arrive here as absent values, but telling
    someone to supply a number they already gave us — during an outage, on every
    upload — is a worse answer than admitting we couldn't look it up.
    """
    elevation_gain = course.profile.elevation_gain_meters

    if course.sport == "cycling":
        # The cycling model inverts avg grade to recover climbing distance, so it
        # wants the grade of the ascending portions — exactly what CourseProfile
        # reports, and the one figure that is genuinely unavailable without an
        # elevation stream. Only this branch has to refuse.
        if course.profile.avg_grade_pct is None and elevation_gain > 0:
            return None, UNKNOWN_CLIMB_GRADE_REASON
        return _analyze_cycling(course, athlete, course.profile.avg_grade_pct or 0.0)
    if course.sport == "running":
        # The running model uses grade the other way round: it adds a GAP penalty of
        # roughly 12 s/km per 1% across the *whole* distance. Handing it the
        # ascending-portions grade penalises the flat and descending kilometres as
        # well — on a 40 km course with 1000 m of gain that is +60 s/km applied to
        # all 40 km instead of +30, some twenty minutes of invented finish time. Give
        # it the grade averaged over the whole route instead.
        return _analyze_running(course, athlete, _overall_grade_pct(course))
    if course.sport == "hiking" or elevation_gain > HIGH_ALTITUDE_GAIN_METERS:
        # Big vertical is worth advising on whatever the file called itself, and
        # this analyzer needs nothing from the athlete.
        return analyze_mountain_objective(elevation_gain_meters=elevation_gain), None

    return None, (
        f"Sport is '{course.sport}', so there's no course model to apply. "
        "Ask the athlete which sport this course is for."
    )


def _analyze_cycling(
    course: ParsedCourse,
    athlete: AthleteCourseContext,
    avg_grade: float,
) -> tuple[CourseAnalysis | None, str | None]:
    ftp_watts = _threshold_for(athlete.thresholds, "cycling", lambda t: t.lt2_power_watts)
    weight_kg = athlete.profile.weight_kg if athlete.profile else None

    if ftp_watts is None or weight_kg is None:
        if athlete.lookup_failed:
            return None, LOOKUP_FAILED_REASON
        missing = [
            label
            for label, value in (("an FTP", ftp_watts), ("a body weight", weight_kg))
            if value is None
        ]
        return None, (
            f"Estimating this ride needs {' and '.join(missing)} on file. "
            "Ask the athlete, then re-run the analysis."
        )

    return (
        analyze_cycling_climb(
            distance_meters=course.profile.distance_meters,
            elevation_gain_meters=course.profile.elevation_gain_meters,
            avg_grade_pct=avg_grade,
            ftp_watts=ftp_watts,
            weight_kg=weight_kg,
        ),
        None,
    )


def _analyze_running(
    course: ParsedCourse,
    athlete: AthleteCourseContext,
    overall_grade: float,
) -> tuple[CourseAnalysis | None, str | None]:
    lt2_pace = _threshold_for(athlete.thresholds, "running", lambda t: t.lt2_pace_sec_per_km)
    if lt2_pace is None:
        if athlete.lookup_failed:
            return None, LOOKUP_FAILED_REASON
        return None, (
            "Estimating this run needs a threshold pace on file. "
            "Ask the athlete for a recent race or test result."
        )

    return (
        analyze_running_climb(
            distance_meters=course.profile.distance_meters,
            elevation_gain_meters=course.profile.elevation_gain_meters,
            avg_grade_pct=overall_grade,
            lt2_pace_sec_km=lt2_pace,
        ),
        None,
    )


def _overall_grade_pct(course: ParsedCourse) -> float:
    """Elevation gain as a percentage of the *whole* route distance.

    Distinct from ``CourseProfile.avg_grade_pct``, which averages over the ascending
    portions only. Which one a model wants depends on how it spends the number:
    ``analyze_cycling_climb`` inverts it to recover climbing distance and needs the
    ascending figure, while ``analyze_running_climb`` multiplies a per-kilometre
    penalty across the entire route and needs this one.
    """
    if course.profile.distance_meters <= 0:
        return 0.0
    return course.profile.elevation_gain_meters / course.profile.distance_meters * 100


def _threshold_for(
    thresholds: list[SportThreshold],
    sport: str,
    read: Callable[[SportThreshold], int | None],
) -> int | None:
    """First non-null reading among the athlete's active thresholds for ``sport``.

    ``get_active_thresholds`` orders newest first, so the first hit is the current
    one. A sport can have several rows where only some carry the field we want.

    Takes an accessor rather than a field name on purpose. ``getattr`` with a string
    fails silently on a typo or a renamed model field — it returns ``None``, which is
    indistinguishable here from "this athlete has no FTP", so the athlete would be
    asked for a number they had already given us and no error would ever surface.
    A lambda is checked.
    """
    for threshold in thresholds:
        if threshold.sport != sport:
            continue
        value = read(threshold)
        if value is not None:
            return int(value)
    return None


def _analysis_to_dict(analysis: CourseAnalysis) -> dict[str, Any]:
    return {
        "estimated_duration_seconds": analysis.estimated_duration_seconds,
        "primary_training_emphasis": analysis.primary_training_emphasis,
        "workout_type_weights": analysis.workout_type_weights,
        "vam_target": analysis.vam_target,
        "notes": analysis.notes,
    }
