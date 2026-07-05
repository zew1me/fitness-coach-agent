"""Tests for backend/services/recalibration.py — threshold recalibration evaluation."""

from datetime import date

from backend.models.athlete import SportThreshold, ThresholdSource
from backend.models.training import Activity
from backend.services.recalibration import evaluate_all, evaluate_recalibration

TODAY = date(2026, 7, 4)
USER_ID = "athlete-1"


def _running_activity(
    *,
    activity_id: str = "a1",
    activity_date: date = TODAY,
    distance_meters: float | None = 5000,
    duration_seconds: int | None = 1080,  # 18:00 5K
    rpe: int | None = None,
) -> Activity:
    return Activity(
        id=activity_id,
        user_id=USER_ID,
        sport="running",
        activity_date=activity_date,
        distance_meters=distance_meters,
        duration_seconds=duration_seconds,
        rpe=rpe,
    )


def _cycling_activity(
    *,
    activity_id: str = "a1",
    activity_date: date = TODAY,
    duration_seconds: int | None = 1200,  # 20 min
    avg_power_watts: int | None = 250,
    rpe: int | None = None,
) -> Activity:
    return Activity(
        id=activity_id,
        user_id=USER_ID,
        sport="cycling",
        activity_date=activity_date,
        duration_seconds=duration_seconds,
        avg_power_watts=avg_power_watts,
        rpe=rpe,
    )


def _threshold(
    *,
    sport: str = "running",
    lt2_pace_sec_per_km: int | None = None,
    lt1_pace_sec_per_km: int | None = None,
    lt2_power_watts: int | None = None,
    lt1_power_watts: int | None = None,
    source: ThresholdSource | None = "estimated",
) -> SportThreshold:
    return SportThreshold(
        user_id=USER_ID,
        sport=sport,
        lt2_pace_sec_per_km=lt2_pace_sec_per_km,
        lt1_pace_sec_per_km=lt1_pace_sec_per_km,
        lt2_power_watts=lt2_power_watts,
        lt1_power_watts=lt1_power_watts,
        source=source,
        estimation_method="model_estimate" if source == "estimated" else "manual",
    )


class TestRunningEligibility:
    def test_high_rpe_5k_recalibrates_with_high_confidence(self) -> None:
        activity = _running_activity(rpe=9)
        result = evaluate_recalibration("running", [activity], None, USER_ID, today=TODAY)

        assert result.status == "recalibrated"
        assert result.confidence == "high"
        assert result.candidate is not None
        assert result.candidate.estimation_method == "race_time"
        assert result.candidate.source == "estimated"
        assert result.candidate.lt2_pace_sec_per_km is not None
        assert result.evidence_activity_id == "a1"

    def test_medium_rpe_recalibrates_with_medium_confidence(self) -> None:
        activity = _running_activity(rpe=8)
        result = evaluate_recalibration("running", [activity], None, USER_ID, today=TODAY)

        assert result.status == "recalibrated"
        assert result.confidence == "medium"

    def test_easy_effort_with_no_rpe_and_no_current_is_ineligible(self) -> None:
        activity = _running_activity(rpe=None)
        result = evaluate_recalibration("running", [activity], None, USER_ID, today=TODAY)

        assert result.status == "insufficient_evidence"
        assert result.candidate is None

    def test_low_rpe_effort_is_excluded_even_with_current_threshold(self) -> None:
        current = _threshold(lt2_pace_sec_per_km=300, lt1_pace_sec_per_km=330)
        easy = _running_activity(rpe=5, duration_seconds=1800)  # slow, low effort
        result = evaluate_recalibration("running", [easy], current, USER_ID, today=TODAY)

        assert result.status == "insufficient_evidence"

    def test_no_rpe_but_meets_current_pace_is_high_confidence(self) -> None:
        current = _threshold(lt2_pace_sec_per_km=250, lt1_pace_sec_per_km=280)
        # 1080s / 5km = 216 sec/km, faster than current 250 sec/km LT2 pace.
        activity = _running_activity(rpe=None, duration_seconds=1080)
        result = evaluate_recalibration("running", [activity], current, USER_ID, today=TODAY)

        assert result.status == "recalibrated"
        assert result.confidence == "high"


class TestCyclingEligibility:
    def test_eligible_power_test_recalibrates(self) -> None:
        activity = _cycling_activity(rpe=9, avg_power_watts=280, duration_seconds=1200)
        result = evaluate_recalibration("cycling", [activity], None, USER_ID, today=TODAY)

        assert result.status == "recalibrated"
        assert result.candidate is not None
        assert result.candidate.estimation_method == "field_test"
        assert result.candidate.lt2_power_watts is not None

    def test_no_power_data_is_ineligible(self) -> None:
        activity = _cycling_activity(avg_power_watts=None, rpe=9)
        result = evaluate_recalibration("cycling", [activity], None, USER_ID, today=TODAY)

        assert result.status == "insufficient_evidence"

    def test_duration_outside_test_window_is_ineligible(self) -> None:
        activity = _cycling_activity(duration_seconds=2700, rpe=9)  # 45 min, no recognized window
        result = evaluate_recalibration("cycling", [activity], None, USER_ID, today=TODAY)

        assert result.status == "insufficient_evidence"


class TestWinnerSelection:
    def test_best_performance_wins_over_most_recent(self) -> None:
        current = _threshold(lt2_pace_sec_per_km=None, lt1_pace_sec_per_km=None)
        faster_older = _running_activity(
            activity_id="older-faster", activity_date=date(2026, 6, 1), rpe=9, duration_seconds=1000
        )
        slower_newer = _running_activity(
            activity_id="newer-slower", activity_date=date(2026, 7, 1), rpe=9, duration_seconds=1200
        )
        result = evaluate_recalibration(
            "running", [slower_newer, faster_older], current, USER_ID, today=TODAY
        )

        assert result.evidence_activity_id == "older-faster"


class TestUnsupportedSport:
    def test_swimming_returns_insufficient_evidence(self) -> None:
        result = evaluate_recalibration("swimming", [], None, USER_ID, today=TODAY)

        assert result.status == "insufficient_evidence"
        assert result.candidate is None


class TestUserConfirmedGuard:
    def test_user_confirmed_threshold_is_never_superseded(self) -> None:
        current = _threshold(lt2_pace_sec_per_km=300, lt1_pace_sec_per_km=330, source="user")
        activity = _running_activity(rpe=9)
        result = evaluate_recalibration("running", [activity], current, USER_ID, today=TODAY)

        assert result.status == "already_user_confirmed"
        assert result.candidate is None


class TestNoChange:
    def test_matching_candidate_within_epsilon_reports_no_change(self) -> None:
        activity = _running_activity(rpe=9, duration_seconds=1080, distance_meters=5000)
        # First pass establishes the "current" candidate values.
        first = evaluate_recalibration("running", [activity], None, USER_ID, today=TODAY)
        assert first.candidate is not None

        # Re-running against the same winning activity, now as "current", should be a no-op.
        second = evaluate_recalibration(
            "running", [activity], first.candidate, USER_ID, today=TODAY
        )

        assert second.status == "no_change"
        assert second.candidate is None


class TestCandidateCarriesForwardOtherFields:
    def test_candidate_preserves_unrelated_fields_and_recomputes_zones(self) -> None:
        current = _threshold(lt2_pace_sec_per_km=300, lt1_pace_sec_per_km=330)
        current.lt2_hr_bpm = 165
        current.css_sec_per_100 = 90
        activity = _running_activity(rpe=9)

        result = evaluate_recalibration("running", [activity], current, USER_ID, today=TODAY)

        assert result.candidate is not None
        assert result.candidate.css_sec_per_100 == 90
        assert result.candidate.lt2_hr_bpm == 165
        assert result.candidate.zones


class TestEvaluateAll:
    def test_evaluates_every_sport_with_activities_or_a_current_threshold(self) -> None:
        activities_by_sport = {
            "running": [_running_activity(rpe=9)],
            "cycling": [],
        }
        current_by_sport = {"swimming": _threshold(sport="swimming")}

        results = evaluate_all(activities_by_sport, current_by_sport, USER_ID, today=TODAY)
        statuses = {r.sport: r.status for r in results}

        assert statuses["running"] == "recalibrated"
        assert statuses["cycling"] == "insufficient_evidence"
        assert statuses["swimming"] == "insufficient_evidence"
