"""Tests for backend/services/compliance.py — plan↔activity matching and the compliance summary."""

from datetime import date

from backend.models.training import Activity, PlanWorkout, TrainingPlan
from backend.services.compliance import (
    COMPLIANCE_WINDOW_DAYS,
    UNCONFIRMED_MAX_AGE_DAYS,
    UNCONFIRMED_MAX_ITEMS,
    build_compliance_summary,
    compliance_window,
    match_activities_to_workouts,
)

TODAY = date(2026, 7, 3)


def _workout(
    *,
    workout_id: str = "w1",
    workout_date: date,
    sport: str = "cycling",
    workout_type: str = "endurance",
    target_duration_minutes: int | None = 60,
    target_tss: float | None = None,
    status: str = "scheduled",
    actual_activity_id: str | None = None,
) -> PlanWorkout:
    return PlanWorkout(
        id=workout_id,
        plan_id="plan-1",
        user_id="athlete-1",
        workout_date=workout_date,
        day_of_week=workout_date.weekday(),
        week_number=1,
        sport=sport,
        title=f"{sport} {workout_type}",
        workout_type=workout_type,
        target_duration_minutes=target_duration_minutes,
        target_tss=target_tss,
        status=status,
        actual_activity_id=actual_activity_id,
    )


def _activity(
    *,
    activity_id: str = "a1",
    activity_date: date,
    sport: str = "cycling",
    duration_seconds: int | None = 3600,
    tss: float | None = None,
    planned_workout_id: str | None = None,
) -> Activity:
    return Activity(
        id=activity_id,
        user_id="athlete-1",
        sport=sport,
        activity_date=activity_date,
        duration_seconds=duration_seconds,
        tss=tss,
        planned_workout_id=planned_workout_id,
    )


def _plan(start: date, end: date | None = None) -> TrainingPlan:
    return TrainingPlan(
        id="plan-1",
        user_id="athlete-1",
        title="Base build",
        plan_type="full_cycle",
        start_date=start,
        end_date=end or start.replace(month=12),
    )


class TestMatching:
    def test_same_day_same_sport_matches(self) -> None:
        workout = _workout(workout_date=date(2026, 7, 1))
        activity = _activity(activity_date=date(2026, 7, 1))
        matches = match_activities_to_workouts([workout], [activity], today=TODAY)
        assert len(matches) == 1
        assert matches[0].workout.id == "w1"
        assert matches[0].activity.id == "a1"

    def test_sport_mismatch_does_not_match(self) -> None:
        workout = _workout(workout_date=date(2026, 7, 1), sport="running")
        activity = _activity(activity_date=date(2026, 7, 1), sport="cycling")
        assert match_activities_to_workouts([workout], [activity], today=TODAY) == []

    def test_sport_comparison_is_case_insensitive(self) -> None:
        workout = _workout(workout_date=date(2026, 7, 1), sport="Cycling")
        activity = _activity(activity_date=date(2026, 7, 1), sport="cycling")
        assert len(match_activities_to_workouts([workout], [activity], today=TODAY)) == 1

    def test_adjacent_day_matches(self) -> None:
        workout = _workout(workout_date=date(2026, 7, 1))
        activity = _activity(activity_date=date(2026, 7, 2))
        assert len(match_activities_to_workouts([workout], [activity], today=TODAY)) == 1

    def test_two_days_apart_does_not_match(self) -> None:
        workout = _workout(workout_date=date(2026, 6, 29))
        activity = _activity(activity_date=date(2026, 7, 1))
        assert match_activities_to_workouts([workout], [activity], today=TODAY) == []

    def test_same_day_candidate_wins_over_adjacent_day(self) -> None:
        same_day = _workout(workout_id="w-same", workout_date=date(2026, 7, 1))
        adjacent = _workout(workout_id="w-adjacent", workout_date=date(2026, 6, 30))
        activity = _activity(activity_date=date(2026, 7, 1))
        matches = match_activities_to_workouts([adjacent, same_day], [activity], today=TODAY)
        assert len(matches) == 1
        assert matches[0].workout.id == "w-same"

    def test_each_activity_matches_at_most_one_workout(self) -> None:
        first = _workout(workout_id="w1", workout_date=date(2026, 7, 1))
        second = _workout(workout_id="w2", workout_date=date(2026, 7, 1))
        activity = _activity(activity_date=date(2026, 7, 1))
        matches = match_activities_to_workouts([first, second], [activity], today=TODAY)
        assert len(matches) == 1

    def test_two_activities_two_workouts_pair_by_duration(self) -> None:
        long_workout = _workout(
            workout_id="w-long", workout_date=date(2026, 7, 1), target_duration_minutes=180
        )
        short_workout = _workout(
            workout_id="w-short", workout_date=date(2026, 7, 1), target_duration_minutes=45
        )
        long_activity = _activity(
            activity_id="a-long", activity_date=date(2026, 7, 1), duration_seconds=175 * 60
        )
        short_activity = _activity(
            activity_id="a-short", activity_date=date(2026, 7, 1), duration_seconds=50 * 60
        )
        matches = match_activities_to_workouts(
            [long_workout, short_workout], [short_activity, long_activity], today=TODAY
        )
        pairs = {m.workout.id: m.activity.id for m in matches}
        assert pairs == {"w-long": "a-long", "w-short": "a-short"}

    def test_wildly_different_duration_disqualifies(self) -> None:
        workout = _workout(workout_date=date(2026, 7, 1), target_duration_minutes=180)
        activity = _activity(activity_date=date(2026, 7, 1), duration_seconds=30 * 60)
        assert match_activities_to_workouts([workout], [activity], today=TODAY) == []

    def test_missing_duration_still_matches_on_date_and_sport(self) -> None:
        workout = _workout(workout_date=date(2026, 7, 1), target_duration_minutes=None)
        activity = _activity(activity_date=date(2026, 7, 1), duration_seconds=None)
        assert len(match_activities_to_workouts([workout], [activity], today=TODAY)) == 1

    def test_already_linked_activity_is_skipped(self) -> None:
        workout = _workout(workout_date=date(2026, 7, 1))
        activity = _activity(activity_date=date(2026, 7, 1), planned_workout_id="w-other")
        assert match_activities_to_workouts([workout], [activity], today=TODAY) == []

    def test_already_resolved_workout_is_skipped(self) -> None:
        workout = _workout(workout_date=date(2026, 7, 1), status="completed")
        activity = _activity(activity_date=date(2026, 7, 1))
        assert match_activities_to_workouts([workout], [activity], today=TODAY) == []

    def test_rest_workout_is_never_matched(self) -> None:
        workout = _workout(workout_date=date(2026, 7, 1), workout_type="rest")
        activity = _activity(activity_date=date(2026, 7, 1))
        assert match_activities_to_workouts([workout], [activity], today=TODAY) == []

    def test_future_workout_is_not_matched(self) -> None:
        workout = _workout(workout_date=TODAY.replace(day=10))
        activity = _activity(activity_date=TODAY.replace(day=10))
        assert match_activities_to_workouts([workout], [activity], today=TODAY) == []


class TestComplianceWindow:
    def test_window_starts_at_plan_start_when_recent(self) -> None:
        start, end = compliance_window(date(2026, 6, 25), TODAY)
        assert start == date(2026, 6, 25)
        assert end == TODAY

    def test_window_caps_at_28_days(self) -> None:
        start, end = compliance_window(date(2026, 1, 1), TODAY)
        assert (end - start).days == COMPLIANCE_WINDOW_DAYS - 1
        assert end == TODAY


class TestSummary:
    def test_summary_counts_and_percentage(self) -> None:
        plan = _plan(date(2026, 6, 1))
        planned = [
            _workout(
                workout_id="w-done",
                workout_date=date(2026, 6, 29),
                status="completed",
                actual_activity_id="a1",
            ),
            _workout(workout_id="w-skipped", workout_date=date(2026, 6, 30), status="skipped"),
            _workout(workout_id="w-unconfirmed", workout_date=date(2026, 7, 1)),
            _workout(workout_id="w-rest", workout_date=date(2026, 7, 2), workout_type="rest"),
            _workout(workout_id="w-future", workout_date=date(2026, 7, 6)),
        ]
        summary = build_compliance_summary(plan, planned, [], today=TODAY)
        assert summary["status"] == "ok"
        totals = summary["totals"]
        assert totals["planned"] == 3  # past, non-rest
        assert totals["completed"] == 1
        assert totals["skipped"] == 1
        assert totals["unconfirmed"] == 1
        assert totals["upcoming"] == 1
        assert summary["compliance_pct"] == round(100 / 3, 1)

    def test_unconfirmed_sessions_capped_and_newest_first(self) -> None:
        plan = _plan(date(2026, 6, 1))
        planned = [
            _workout(workout_id=f"w{i}", workout_date=date(2026, 6, 20 + i)) for i in range(5)
        ]
        summary = build_compliance_summary(plan, planned, [], today=TODAY)
        sessions = summary["unconfirmed_sessions"]
        assert len(sessions) == UNCONFIRMED_MAX_ITEMS
        dates = [s["workout_date"] for s in sessions]
        assert dates == sorted(dates, reverse=True)
        assert dates[0] == "2026-06-24"

    def test_unconfirmed_sessions_exclude_stale_entries(self) -> None:
        plan = _plan(date(2026, 1, 1))
        stale_date = TODAY.fromordinal(TODAY.toordinal() - (UNCONFIRMED_MAX_AGE_DAYS + 1))
        fresh_date = TODAY.fromordinal(TODAY.toordinal() - 2)
        planned = [
            _workout(workout_id="w-stale", workout_date=stale_date),
            _workout(workout_id="w-fresh", workout_date=fresh_date),
        ]
        summary = build_compliance_summary(plan, planned, [], today=TODAY)
        session_ids = [s["id"] for s in summary["unconfirmed_sessions"]]
        assert session_ids == ["w-fresh"]

    def test_unplanned_activities_listed(self) -> None:
        plan = _plan(date(2026, 6, 1))
        matched = _activity(
            activity_id="a-matched",
            activity_date=date(2026, 7, 1),
            planned_workout_id="w1",
        )
        extra = _activity(activity_id="a-extra", activity_date=date(2026, 7, 2), sport="swimming")
        summary = build_compliance_summary(plan, [], [matched, extra], today=TODAY)
        unplanned = summary["unplanned_activities"]
        assert [a["id"] for a in unplanned] == ["a-extra"]

    def test_weekly_breakdown_is_monday_aligned(self) -> None:
        plan = _plan(date(2026, 6, 22))  # a Monday
        planned = [
            _workout(
                workout_id="w1",
                workout_date=date(2026, 6, 23),
                status="completed",
                actual_activity_id="a1",
            ),
            _workout(workout_id="w2", workout_date=date(2026, 6, 30)),
        ]
        summary = build_compliance_summary(plan, planned, [], today=TODAY)
        weeks = summary["weeks"]
        assert len(weeks) == 2
        assert weeks[0]["start"] == "2026-06-22"
        assert weeks[0]["completed"] == 1
        assert weeks[1]["unconfirmed"] == 1

    def test_modified_counts_toward_compliance(self) -> None:
        plan = _plan(date(2026, 6, 1))
        planned = [
            _workout(workout_id="w1", workout_date=date(2026, 7, 1), status="modified"),
        ]
        summary = build_compliance_summary(plan, planned, [], today=TODAY)
        assert summary["compliance_pct"] == 100.0

    def test_future_resolved_workout_stays_out_of_compliance(self) -> None:
        plan = _plan(date(2026, 6, 1))
        planned = [
            _workout(
                workout_id="w-future-done",
                workout_date=date(2026, 7, 6),
                status="completed",
                actual_activity_id="a1",
            ),
        ]
        summary = build_compliance_summary(plan, planned, [], today=TODAY)
        assert summary["totals"]["planned"] == 0
        assert summary["totals"]["upcoming"] == 1
        assert summary["compliance_pct"] is None

    def test_todays_resolved_workout_counts_as_past(self) -> None:
        plan = _plan(date(2026, 6, 1))
        planned = [
            _workout(
                workout_id="w-today-done",
                workout_date=TODAY,
                status="completed",
                actual_activity_id="a1",
            ),
        ]
        summary = build_compliance_summary(plan, planned, [], today=TODAY)
        assert summary["totals"]["planned"] == 1
        assert summary["compliance_pct"] == 100.0

    def test_no_past_workouts_yields_null_percentage(self) -> None:
        plan = _plan(TODAY)
        planned = [_workout(workout_date=TODAY.replace(day=10))]
        summary = build_compliance_summary(plan, planned, [], today=TODAY)
        assert summary["compliance_pct"] is None
