"""Turn a parsed course into something the coach can advise from.

Pairs the course's terrain (``backend/engine/course_profile.py``) with the athlete's
own thresholds so ``backend/engine/course_analyzer.py`` can estimate a finish time,
a VAM target, and where training should be pointed.

Everything here degrades rather than fails. An athlete who has not given us an FTP
yet should get the terrain plus a plain reason the estimate is missing — that is the
coach's cue to ask — never a 5xx that loses the upload.
"""

from __future__ import annotations

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


def build_course_payload(
    course: ParsedCourse,
    *,
    profile: AthleteProfile | None,
    thresholds: list[SportThreshold],
    source_file_key: str | None = None,
    public_url: str | None = None,
) -> dict[str, Any]:
    """Assemble the tool result for an uploaded course.

    ``kind`` is the discriminator the coach reads to know nothing was logged. It
    matches the shape already used for activity entries in the zip response.
    """
    analysis, unavailable_reason = analyze_course(course, profile=profile, thresholds=thresholds)

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


def analyze_course(
    course: ParsedCourse,
    *,
    profile: AthleteProfile | None,
    thresholds: list[SportThreshold],
) -> tuple[CourseAnalysis | None, str | None]:
    """Pick and run the analyzer that fits this course, or explain why we can't.

    Returns ``(analysis, reason)`` with exactly one of them set.
    """
    elevation_gain = course.profile.elevation_gain_meters
    # `analyze_*_climb` inverts avg grade to recover climbing distance and guards
    # against a non-positive value, so 0.0 is a safe stand-in for "unknown". The
    # payload still reports the real `None` — this substitution never escapes.
    avg_grade = course.profile.avg_grade_pct or 0.0

    if course.sport == "cycling":
        return _analyze_cycling(course, profile, thresholds, avg_grade)
    if course.sport == "running":
        return _analyze_running(course, thresholds, avg_grade)
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
    profile: AthleteProfile | None,
    thresholds: list[SportThreshold],
    avg_grade: float,
) -> tuple[CourseAnalysis | None, str | None]:
    ftp_watts = _threshold_for(thresholds, "cycling", "lt2_power_watts")
    weight_kg = profile.weight_kg if profile else None

    if ftp_watts is None or weight_kg is None:
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
    thresholds: list[SportThreshold],
    avg_grade: float,
) -> tuple[CourseAnalysis | None, str | None]:
    lt2_pace = _threshold_for(thresholds, "running", "lt2_pace_sec_per_km")
    if lt2_pace is None:
        return None, (
            "Estimating this run needs a threshold pace on file. "
            "Ask the athlete for a recent race or test result."
        )

    return (
        analyze_running_climb(
            distance_meters=course.profile.distance_meters,
            elevation_gain_meters=course.profile.elevation_gain_meters,
            avg_grade_pct=avg_grade,
            lt2_pace_sec_km=lt2_pace,
        ),
        None,
    )


def _threshold_for(thresholds: list[SportThreshold], sport: str, field: str) -> int | None:
    """First non-null value of ``field`` among the athlete's active thresholds for ``sport``.

    ``get_active_thresholds`` orders newest first, so the first hit is the current
    one. A sport can have several rows where only some carry the field we want.
    """
    for threshold in thresholds:
        if threshold.sport != sport:
            continue
        value = getattr(threshold, field, None)
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
