"""Turn a parsed course into something the coach can advise from.

Pairs the course's terrain (``backend/engine/course_profile.py``) with the athlete's
own thresholds so ``backend/engine/course_analyzer.py`` can estimate a finish time,
a VAM target, and where training should be pointed.

Everything here degrades rather than fails. An athlete who has not given us an FTP
yet should get the terrain plus a plain reason the estimate is missing — that is the
coach's cue to ask — never a 5xx that loses the upload.
"""

from __future__ import annotations

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
    """
    Build a coach-facing payload containing course metadata and available analysis.
    
    Parameters:
        athlete (AthleteCourseContext): Athlete data and threshold lookup state used
            to determine whether sport-specific analysis is available.
        source_file_key (str | None): Optional key identifying the uploaded source file.
        public_url (str | None): Optional public URL for the uploaded course.
    
    Returns:
        dict[str, Any]: Payload containing course metadata, serialized analysis when
            available, and a reason when analysis cannot be provided.
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
    """
    Selects the appropriate analysis model for a parsed course.
    
    Parameters:
        athlete (AthleteCourseContext): Athlete data and threshold lookup status used for sport-specific analyses.
    
    Returns:
        tuple[CourseAnalysis | None, str | None]: The course analysis and no reason, or no analysis and an explanation of why analysis is unavailable.
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
    """
    Analyze a cycling course using the athlete's FTP and body weight.
    
    Parameters:
        course (ParsedCourse): The parsed cycling course to analyze.
        athlete (AthleteCourseContext): Athlete profile and threshold data used for estimation.
        avg_grade (float): The course's average climbing grade as a percentage.
    
    Returns:
        tuple[CourseAnalysis | None, str | None]: The course analysis and no reason when successful, or no analysis and a reason when required athlete data is unavailable.
    """
    ftp_watts = _threshold_for(athlete.thresholds, "cycling", "lt2_power_watts")
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
    """
    Analyze a running course using the athlete's threshold pace.
    
    Parameters:
        course (ParsedCourse): Parsed course data to analyze.
        athlete (AthleteCourseContext): Athlete profile, thresholds, and lookup status.
        overall_grade (float): Elevation gain as a percentage of the full route distance.
    
    Returns:
        tuple[CourseAnalysis | None, str | None]: The course analysis and no reason, or no analysis and a reason when the threshold pace is unavailable.
    """
    lt2_pace = _threshold_for(athlete.thresholds, "running", "lt2_pace_sec_per_km")
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
    """Calculate elevation gain as a percentage of the total route distance.
    
    Returns:
    	float: Elevation gain percentage, or 0.0 when the route distance is zero or negative.
    """
    if course.profile.distance_meters <= 0:
        return 0.0
    return course.profile.elevation_gain_meters / course.profile.distance_meters * 100


def _threshold_for(thresholds: list[SportThreshold], sport: str, attribute: str) -> int | None:
    """Find the most recent available threshold value for a sport and attribute.
    
    Parameters:
        thresholds (list[SportThreshold]): Athlete thresholds ordered from newest to oldest.
        sport (str): Sport whose threshold should be selected.
        attribute (str): Threshold field to retrieve.
    
    Returns:
        int | None: The first available threshold value, or ``None`` if no matching value exists.
    """
    for threshold in thresholds:
        if threshold.sport != sport:
            continue
        value = getattr(threshold, attribute, None)
        if value is not None:
            return int(value)
    return None


def _analysis_to_dict(analysis: CourseAnalysis) -> dict[str, Any]:
    """Convert a course analysis into a serializable dictionary of analysis results.
    
    Parameters:
    	analysis (CourseAnalysis): The analysis to serialize.
    
    Returns:
    	dict[str, Any]: The analysis fields and their corresponding values.
    """
    return {
        "estimated_duration_seconds": analysis.estimated_duration_seconds,
        "primary_training_emphasis": analysis.primary_training_emphasis,
        "workout_type_weights": analysis.workout_type_weights,
        "vam_target": analysis.vam_target,
        "notes": analysis.notes,
    }
