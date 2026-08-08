"""Course analysis: picking the right model, and degrading when we can't."""

from __future__ import annotations

import pytest

from backend.engine.course_profile import CourseProfile
from backend.engine.gpx_parser import ParsedCourse
from backend.models.athlete import AthleteProfile, SportThreshold
from backend.services.course import analyze_course, build_course_payload


def _course(
    sport: str,
    *,
    distance_meters: float = 94490.2,
    elevation_gain_meters: float = 2308.6,
    avg_grade_pct: float | None = 6.0,
    max_grade_pct: float | None = 14.2,
    name: str | None = "Schotterfest Long",
) -> ParsedCourse:
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


def _profile(weight_kg: float | None = 72.0) -> AthleteProfile:
    return AthleteProfile(user_id="athlete-1", weight_kg=weight_kg)


def test_cycling_course_with_ftp_and_weight_gets_a_vam_target() -> None:
    analysis, reason = analyze_course(
        _course("cycling"),
        profile=_profile(),
        thresholds=[_threshold("cycling", lt2_power_watts=260)],
    )

    assert reason is None
    assert analysis is not None
    assert analysis.primary_training_emphasis == "climbing_power"
    assert analysis.vam_target is not None
    assert analysis.vam_target > 0
    assert analysis.estimated_duration_seconds is not None


def test_cycling_course_without_an_ftp_explains_itself_instead_of_failing() -> None:
    analysis, reason = analyze_course(
        _course("cycling"),
        profile=_profile(),
        thresholds=[],
    )

    assert analysis is None
    assert reason is not None
    assert "FTP" in reason


def test_cycling_course_without_a_weight_explains_itself() -> None:
    analysis, reason = analyze_course(
        _course("cycling"),
        profile=_profile(weight_kg=None),
        thresholds=[_threshold("cycling", lt2_power_watts=260)],
    )

    assert analysis is None
    assert reason is not None
    assert "body weight" in reason


def test_missing_ftp_and_weight_are_named_together_in_one_ask() -> None:
    analysis, reason = analyze_course(_course("cycling"), profile=None, thresholds=[])

    assert analysis is None
    assert reason is not None
    assert "FTP" in reason
    assert "body weight" in reason


def test_running_course_with_a_threshold_pace_gets_a_gap_estimate() -> None:
    analysis, reason = analyze_course(
        _course("running", distance_meters=21097.0, elevation_gain_meters=600.0),
        profile=_profile(),
        thresholds=[_threshold("running", lt2_pace_sec_per_km=255)],
    )

    assert reason is None
    assert analysis is not None
    assert analysis.primary_training_emphasis == "uphill_running_economy"
    assert analysis.estimated_duration_seconds is not None


def test_running_course_without_a_threshold_pace_explains_itself() -> None:
    analysis, reason = analyze_course(_course("running"), profile=_profile(), thresholds=[])

    assert analysis is None
    assert reason is not None
    assert "threshold pace" in reason


def test_big_vertical_reaches_the_mountain_model_without_any_athlete_data() -> None:
    # 3000 m of gain on a course whose sport we could not determine. Worth advising
    # on regardless, and this analyzer needs nothing from the athlete.
    analysis, reason = analyze_course(
        _course("general", elevation_gain_meters=3000.0),
        profile=None,
        thresholds=[],
    )

    assert reason is None
    assert analysis is not None
    assert analysis.primary_training_emphasis == "high_altitude_endurance"


def test_hiking_course_reaches_the_mountain_model_at_any_elevation() -> None:
    analysis, reason = analyze_course(
        _course("hiking", elevation_gain_meters=800.0),
        profile=None,
        thresholds=[],
    )

    assert reason is None
    assert analysis is not None
    assert analysis.primary_training_emphasis == "mountain_endurance"


def test_unknown_sport_with_modest_vertical_asks_which_sport_it_is() -> None:
    analysis, reason = analyze_course(
        _course("general", elevation_gain_meters=300.0),
        profile=_profile(),
        thresholds=[_threshold("cycling", lt2_power_watts=260)],
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

    analysis, reason = analyze_course(
        course,
        profile=_profile(),
        thresholds=[_threshold("cycling", lt2_power_watts=260)],
    )

    assert analysis is None
    assert reason is not None
    assert "grade is unknown" in reason

    payload = build_course_payload(course, profile=_profile(), thresholds=[])
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
        course,
        profile=_profile(),
        thresholds=[_threshold("running", lt2_pace_sec_per_km=255)],
    )

    assert reason is None
    assert analysis is not None


def test_threshold_lookup_skips_rows_missing_the_field_we_need() -> None:
    # A sport can have several active rows where only some carry the value.
    analysis, reason = analyze_course(
        _course("cycling"),
        profile=_profile(),
        thresholds=[
            _threshold("cycling", lt2_hr_bpm=165),
            _threshold("cycling", lt2_power_watts=260),
        ],
    )

    assert reason is None
    assert analysis is not None


def test_threshold_lookup_ignores_other_sports() -> None:
    analysis, reason = analyze_course(
        _course("cycling"),
        profile=_profile(),
        thresholds=[_threshold("running", lt2_power_watts=260)],
    )

    assert analysis is None
    assert reason is not None


def test_payload_marks_the_upload_as_a_course_and_carries_the_file_reference() -> None:
    payload = build_course_payload(
        _course("cycling"),
        profile=_profile(),
        thresholds=[_threshold("cycling", lt2_power_watts=260)],
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
    payload = build_course_payload(_course("cycling"), profile=None, thresholds=[])

    assert payload["kind"] == "course"
    assert payload["analysis"] is None
    assert payload["analysis_unavailable_reason"] is not None
    # Terrain is still there even when the model isn't — that is the point of
    # degrading rather than failing.
    assert payload["course"]["elevation_gain_meters"] == pytest.approx(2308.6)
