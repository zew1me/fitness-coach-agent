"""Course analysis: picking the right model, and degrading when we can't."""

from __future__ import annotations

import pytest

from backend.engine.course_profile import CourseProfile
from backend.engine.gpx_parser import ParsedCourse
from backend.models.athlete import AthleteProfile, SportThreshold
from backend.services.course import (
    LOOKUP_FAILED_REASON,
    AthleteCourseContext,
    analyze_course,
    build_course_payload,
)


def _course(
    sport: str,
    *,
    distance_meters: float = 94490.2,
    elevation_gain_meters: float = 2308.6,
    avg_grade_pct: float | None = 6.0,
    max_grade_pct: float | None = 14.2,
    name: str | None = "Schotterfest Long",
) -> ParsedCourse:
    """Create a parsed course fixture with configurable terrain and name."""
    return ParsedCourse(
        sport=sport,
        profile=CourseProfile(
            distance_meters=distance_meters,
            elevation_gain_meters=elevation_gain_meters,
            avg_grade_pct=avg_grade_pct,
            max_grade_pct=max_grade_pct,
        ),
        name=name,
    )


def _threshold(
    sport: str,
    *,
    lt2_power_watts: int | None = None,
    lt2_pace_sec_per_km: int | None = None,
    lt2_hr_bpm: int | None = None,
) -> SportThreshold:
    return SportThreshold(
        user_id="athlete-1",
        sport=sport,
        lt2_power_watts=lt2_power_watts,
        lt2_pace_sec_per_km=lt2_pace_sec_per_km,
        lt2_hr_bpm=lt2_hr_bpm,
    )


def _athlete(
    *,
    weight_kg: float | None = 72.0,
    with_profile: bool = True,
    thresholds: list[SportThreshold] | None = None,
    lookup_failed: bool = False,
) -> AthleteCourseContext:
    return AthleteCourseContext(
        profile=AthleteProfile(user_id="athlete-1", weight_kg=weight_kg) if with_profile else None,
        thresholds=thresholds or [],
        lookup_failed=lookup_failed,
    )


def _cycling_athlete() -> AthleteCourseContext:
    return _athlete(thresholds=[_threshold("cycling", lt2_power_watts=260)])


def test_cycling_course_with_ftp_and_weight_gets_a_vam_target() -> None:
    analysis, reason = analyze_course(_course("cycling"), athlete=_cycling_athlete())

    assert reason is None
    assert analysis is not None
    assert analysis.primary_training_emphasis == "climbing_power"
    assert analysis.vam_target is not None
    assert analysis.vam_target > 0
    assert analysis.estimated_duration_seconds is not None


def test_cycling_course_without_an_ftp_explains_itself_instead_of_failing() -> None:
    analysis, reason = analyze_course(_course("cycling"), athlete=_athlete())

    assert analysis is None
    assert reason is not None
    assert "FTP" in reason


def test_cycling_course_without_a_weight_explains_itself() -> None:
    analysis, reason = analyze_course(
        _course("cycling"),
        athlete=_athlete(weight_kg=None, thresholds=[_threshold("cycling", lt2_power_watts=260)]),
    )

    assert analysis is None
    assert reason is not None
    assert "body weight" in reason


def test_missing_ftp_and_weight_are_named_together_in_one_ask() -> None:
    analysis, reason = analyze_course(_course("cycling"), athlete=_athlete(with_profile=False))

    assert analysis is None
    assert reason is not None
    assert "FTP" in reason
    assert "body weight" in reason


def test_a_failed_lookup_does_not_blame_the_athlete_for_missing_data() -> None:
    # A failed read and an athlete who never supplied an FTP both arrive here as
    # absent values. During an outage the first would otherwise tell every athlete,
    # on every upload, to re-enter a number they already gave us.
    analysis, reason = analyze_course(
        _course("cycling"), athlete=_athlete(with_profile=False, lookup_failed=True)
    )

    assert analysis is None
    assert reason == LOOKUP_FAILED_REASON
    assert "FTP" not in reason


def test_a_failed_lookup_is_reported_for_running_courses_too() -> None:
    analysis, reason = analyze_course(_course("running"), athlete=_athlete(lookup_failed=True))

    assert analysis is None
    assert reason == LOOKUP_FAILED_REASON


def test_running_course_with_a_threshold_pace_gets_a_gap_estimate() -> None:
    analysis, reason = analyze_course(
        _course("running", distance_meters=21097.0, elevation_gain_meters=600.0),
        athlete=_athlete(thresholds=[_threshold("running", lt2_pace_sec_per_km=255)]),
    )

    assert reason is None
    assert analysis is not None
    assert analysis.primary_training_emphasis == "uphill_running_economy"
    assert analysis.estimated_duration_seconds is not None


def test_running_estimate_uses_grade_over_the_whole_course_not_just_the_climbs() -> None:
    # CourseProfile.avg_grade_pct is the grade of the *ascending* portions, which is
    # what analyze_cycling_climb needs because it inverts it to recover climbing
    # distance. analyze_running_climb uses grade differently: it applies a GAP
    # penalty of ~12 s/km per 1% across the entire distance. Feeding it the
    # ascending-portions grade penalises the flat and descending kilometres too,
    # which on a rolling course overstates the finish time by tens of minutes.
    rolling = _course(
        "running",
        distance_meters=40_000.0,
        elevation_gain_meters=1000.0,
        # Half the course climbs at 5%; over the whole 40 km it averages 2.5%.
        avg_grade_pct=5.0,
    )

    analysis, reason = analyze_course(
        rolling, athlete=_athlete(thresholds=[_threshold("running", lt2_pace_sec_per_km=300)])
    )

    assert reason is None
    assert analysis is not None
    assert analysis.estimated_duration_seconds is not None
    # 2.5% overall means +30 s/km over 40 km, not 5% meaning +60 s/km — about a
    # 20 minute difference in the number the coach quotes the athlete.
    assert analysis.estimated_duration_seconds == pytest.approx((300 + 30) * 40, rel=0.1)


def test_running_course_without_a_threshold_pace_explains_itself() -> None:
    analysis, reason = analyze_course(_course("running"), athlete=_athlete())

    assert analysis is None
    assert reason is not None
    assert "threshold pace" in reason


def test_big_vertical_reaches_the_mountain_model_without_any_athlete_data() -> None:
    # 3000 m of gain on a course whose sport we could not determine. Worth advising
    # on regardless, and this analyzer needs nothing from the athlete.
    analysis, reason = analyze_course(
        _course("general", elevation_gain_meters=3000.0),
        athlete=_athlete(with_profile=False),
    )

    assert reason is None
    assert analysis is not None
    assert analysis.primary_training_emphasis == "high_altitude_endurance"


def test_hiking_course_reaches_the_mountain_model_at_any_elevation() -> None:
    analysis, reason = analyze_course(
        _course("hiking", elevation_gain_meters=800.0),
        athlete=_athlete(with_profile=False),
    )

    assert reason is None
    assert analysis is not None
    assert analysis.primary_training_emphasis == "mountain_endurance"


def test_unknown_sport_with_modest_vertical_asks_which_sport_it_is() -> None:
    analysis, reason = analyze_course(
        _course("general", elevation_gain_meters=300.0), athlete=_cycling_athlete()
    )

    assert analysis is None
    assert reason is not None
    assert "which sport" in reason


def test_unknown_grade_on_a_hilly_course_refuses_to_estimate() -> None:
    # A FIT course with lap totals but no record stream. Passing 0.0 for the unknown
    # grade does not fail loudly — it makes analyze_cycling_climb treat the whole
    # route as flat and bill a full 30 km/h transit on top of the climb, which
    # inflated a 1.64 h estimate to 2.14 h while reporting no reason at all.
    course = _course("cycling", avg_grade_pct=None, max_grade_pct=None)

    analysis, reason = analyze_course(course, athlete=_cycling_athlete())

    assert analysis is None
    assert reason is not None
    assert "grade is unknown" in reason

    payload = build_course_payload(course, athlete=_athlete())
    # Terrain is still reported, and the unknown grade stays honestly unknown.
    assert payload["course"]["avg_grade_pct"] is None
    assert payload["course"]["max_grade_pct"] is None
    assert payload["course"]["elevation_gain_meters"] == pytest.approx(2308.6)


def test_unknown_grade_on_a_flat_course_still_analyzes() -> None:
    # No vertical means no climb to mis-price, so an absent grade costs nothing.
    course = _course(
        "running",
        distance_meters=10_000.0,
        elevation_gain_meters=0.0,
        avg_grade_pct=None,
        max_grade_pct=None,
    )

    analysis, reason = analyze_course(
        course, athlete=_athlete(thresholds=[_threshold("running", lt2_pace_sec_per_km=255)])
    )

    assert reason is None
    assert analysis is not None


def test_threshold_lookup_skips_rows_missing_the_field_we_need() -> None:
    # A sport can have several active rows where only some carry the value.
    analysis, reason = analyze_course(
        _course("cycling"),
        athlete=_athlete(
            thresholds=[
                _threshold("cycling", lt2_hr_bpm=165),
                _threshold("cycling", lt2_power_watts=260),
            ]
        ),
    )

    assert reason is None
    assert analysis is not None


def test_threshold_lookup_ignores_other_sports() -> None:
    analysis, reason = analyze_course(
        _course("cycling"),
        athlete=_athlete(thresholds=[_threshold("running", lt2_power_watts=260)]),
    )

    assert analysis is None
    assert reason is not None


def test_payload_marks_the_upload_as_a_course_and_carries_the_file_reference() -> None:
    payload = build_course_payload(
        _course("cycling"),
        athlete=_cycling_athlete(),
        source_file_key="users/athlete-1/chat-attachment/course.gpx",
        public_url="https://files.example/course.gpx",
    )

    assert payload["kind"] == "course"
    assert payload["status"] == "analyzed"
    assert payload["course"]["sport"] == "cycling"
    assert payload["course"]["name"] == "Schotterfest Long"
    assert payload["course"]["distance_meters"] == pytest.approx(94490.2)
    assert payload["course"]["source_file_key"].endswith("course.gpx")
    assert payload["course"]["public_url"] == "https://files.example/course.gpx"
    assert payload["analysis"] is not None
    assert payload["analysis"]["vam_target"] is not None
    assert payload["analysis_unavailable_reason"] is None


def test_payload_reports_the_reason_when_analysis_is_unavailable() -> None:
    payload = build_course_payload(_course("cycling"), athlete=_athlete(with_profile=False))

    assert payload["kind"] == "course"
    assert payload["analysis"] is None
    assert payload["analysis_unavailable_reason"] is not None
    # Terrain is still there even when the model isn't — that is the point of
    # degrading rather than failing.
    assert payload["course"]["elevation_gain_meters"] == pytest.approx(2308.6)


def test_grade_less_running_course_still_estimates() -> None:
    # The running model derives its own whole-route grade from gain and distance,
    # both of which a lap-totals-only file has. Refusing here would withhold an
    # estimate we are perfectly able to make.
    course = _course(
        "running",
        distance_meters=42195.0,
        elevation_gain_meters=800.0,
        avg_grade_pct=None,
        max_grade_pct=None,
    )

    analysis, reason = analyze_course(
        course,
        athlete=_athlete(thresholds=[_threshold("running", lt2_pace_sec_per_km=255)]),
    )

    assert reason is None
    assert analysis is not None
    assert analysis.estimated_duration_seconds is not None


def test_grade_less_hiking_course_still_reaches_the_mountain_model() -> None:
    course = _course("hiking", elevation_gain_meters=1400.0, avg_grade_pct=None, max_grade_pct=None)

    analysis, reason = analyze_course(course, athlete=_athlete())

    assert reason is None
    assert analysis is not None
    assert analysis.primary_training_emphasis == "mountain_endurance"


def test_grade_less_high_vertical_course_still_reaches_the_mountain_model() -> None:
    # The mountain model needs only elevation gain, which a lap-totals-only file has.
    course = _course(
        "general", elevation_gain_meters=3200.0, avg_grade_pct=None, max_grade_pct=None
    )

    analysis, reason = analyze_course(course, athlete=_athlete())

    assert reason is None
    assert analysis is not None
    assert analysis.primary_training_emphasis == "high_altitude_endurance"


def test_a_big_vertical_cycling_course_still_gets_cycling_advice() -> None:
    # Ordering guard. Routing every high-vertical course to the mountain model
    # regardless of sport looks tidy and is wrong: analyze_mountain_objective
    # prescribes loaded hikes, pack-carrying strength, and summit pushes. A 3000 m
    # gran fondo wants VAM and climbing power. Sport-specific models come first;
    # the mountain model is the fallback for sports we cannot model.
    analysis, reason = analyze_course(
        _course("cycling", distance_meters=120_000.0, elevation_gain_meters=3500.0),
        athlete=_cycling_athlete(),
    )

    assert reason is None
    assert analysis is not None
    assert analysis.primary_training_emphasis == "climbing_power"
    assert analysis.vam_target is not None


def test_a_big_vertical_running_course_still_gets_running_advice() -> None:
    analysis, reason = analyze_course(
        _course("running", distance_meters=50_000.0, elevation_gain_meters=3500.0),
        athlete=_athlete(thresholds=[_threshold("running", lt2_pace_sec_per_km=300)]),
    )

    assert reason is None
    assert analysis is not None
    assert analysis.primary_training_emphasis == "uphill_running_economy"
