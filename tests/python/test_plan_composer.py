"""Tests for backend/services/plan_composer.py — deterministic daily workout composition."""

from datetime import date, timedelta

from backend.engine.periodization import build_plan_skeleton
from backend.models.athlete import ScheduleOverride
from backend.services.plan_composer import PlanComposerPolicy, compose_plan_workouts

START = date(2026, 7, 6)  # a Monday


def _skeleton(*, weeks: int = 4, goal_type: str = "maintenance"):
    return build_plan_skeleton(
        current_ctl=50,
        target_date=START + timedelta(weeks=weeks) if goal_type == "event" else None,
        available_hours_per_week=8,
        goal_type=goal_type,
        recovery_week_frequency=4,
        start_date=START,
    )


def test_composes_seven_workouts_per_week() -> None:
    skeleton = _skeleton()
    workouts = compose_plan_workouts(
        skeleton, user_id="athlete-1", plan_id="plan-1", sport="cycling"
    )
    assert len(workouts) == skeleton.total_weeks * 7
    dates = [w.workout_date for w in workouts]
    assert dates == sorted(dates)
    assert dates[0] == START
    assert dates[-1] == START + timedelta(days=skeleton.total_weeks * 7 - 1)


def test_workout_fields_are_consistent() -> None:
    workouts = compose_plan_workouts(
        _skeleton(), user_id="athlete-1", plan_id="plan-1", sport="cycling"
    )
    for workout in workouts:
        assert workout.user_id == "athlete-1"
        assert workout.plan_id == "plan-1"
        assert workout.status == "scheduled"
        assert workout.day_of_week == workout.workout_date.weekday()
        assert 1 <= workout.week_number <= len(workouts) // 7


def test_week_contains_rest_long_and_quality_days() -> None:
    workouts = compose_plan_workouts(
        _skeleton(), user_id="athlete-1", plan_id="plan-1", sport="cycling"
    )
    week_one = [w for w in workouts if w.week_number == 1]
    types = [w.workout_type for w in week_one]
    assert "rest" in types
    assert "long_ride" in types
    # Training days carry TSS targets that roughly sum to the weekly target.
    weekly_tss = sum(w.target_tss or 0 for w in week_one)
    target = _skeleton().phases[0].target_weekly_tss
    assert abs(weekly_tss - target) / target < 0.15


def test_running_gets_long_run() -> None:
    workouts = compose_plan_workouts(
        _skeleton(), user_id="athlete-1", plan_id="plan-1", sport="running"
    )
    types = {w.workout_type for w in workouts}
    assert "long_run" in types
    assert "long_ride" not in types


def test_recovery_week_has_no_quality_sessions() -> None:
    skeleton = _skeleton(weeks=6)
    recovery_weeks = [p.start_week for p in skeleton.phases if p.focus == "recovery"]
    assert recovery_weeks, "expected at least one recovery week in the skeleton"
    workouts = compose_plan_workouts(
        skeleton, user_id="athlete-1", plan_id="plan-1", sport="cycling"
    )
    recovery_workouts = [w for w in workouts if w.week_number == recovery_weeks[0]]
    assert all(
        w.workout_type in ("rest", "recovery", "endurance", "long_ride") for w in recovery_workouts
    )


def test_build_phase_allows_three_quality_sessions() -> None:
    skeleton = _skeleton(goal_type="improvement")  # rolling build focus, max_hiit 3
    build_weeks = [p.start_week for p in skeleton.phases if p.focus == "build"]
    assert build_weeks
    workouts = compose_plan_workouts(
        skeleton, user_id="athlete-1", plan_id="plan-1", sport="cycling"
    )
    build_week = [w for w in workouts if w.week_number == build_weeks[0]]
    quality = [w for w in build_week if w.workout_type == "threshold"]
    assert len(quality) == 3


def test_longevity_policy_limits_quality_sessions() -> None:
    skeleton = _skeleton(goal_type="improvement")
    workouts = compose_plan_workouts(
        skeleton,
        user_id="athlete-1",
        plan_id="plan-1",
        sport="cycling",
        policy=PlanComposerPolicy(training_model="longevity"),
    )

    build_week = [w for w in workouts if w.week_number == 1]
    quality = [w for w in build_week if w.workout_type in {"tempo", "threshold", "vo2max"}]

    assert len(quality) == 1
    assert quality[0].workout_type == "tempo"


def test_recovery_return_policy_avoids_quality_and_adds_rest() -> None:
    skeleton = _skeleton(goal_type="improvement")
    workouts = compose_plan_workouts(
        skeleton,
        user_id="athlete-1",
        plan_id="plan-1",
        sport="cycling",
        policy=PlanComposerPolicy(training_model="recovery_return"),
    )

    week_one = [w for w in workouts if w.week_number == 1]
    quality = [w for w in week_one if w.workout_type in {"tempo", "threshold", "vo2max"}]
    rest_days = [w for w in week_one if w.workout_type == "rest"]

    assert quality == []
    assert len(rest_days) >= 2


def test_unavailable_days_become_rest() -> None:
    pattern = {
        "monday": {"available": False},
        "wednesday": {"available": False},
    }
    workouts = compose_plan_workouts(
        _skeleton(),
        user_id="athlete-1",
        plan_id="plan-1",
        sport="cycling",
        weekly_pattern=pattern,
    )
    week_one = {w.workout_date.weekday(): w for w in workouts if w.week_number == 1}
    assert week_one[0].workout_type == "rest"  # Monday
    assert week_one[2].workout_type == "rest"  # Wednesday


def test_non_monday_start_keeps_weekday_semantics() -> None:
    from datetime import date

    friday = date(2026, 7, 10)
    skeleton = build_plan_skeleton(
        current_ctl=50,
        target_date=None,
        available_hours_per_week=8,
        goal_type="maintenance",
        recovery_week_frequency=4,
        start_date=friday,
    )
    pattern = {"monday": {"available": False}}
    workouts = compose_plan_workouts(
        skeleton,
        user_id="athlete-1",
        plan_id="plan-1",
        sport="cycling",
        weekly_pattern=pattern,
    )
    # Every calendar Monday is rest, regardless of the Friday plan start.
    mondays = [w for w in workouts if w.workout_date.weekday() == 0]
    assert mondays
    assert all(w.workout_type == "rest" for w in mondays)
    # The long session still lands on a weekend day.
    week_one = [w for w in workouts if w.week_number == 1]
    long_days = [w.workout_date.weekday() for w in week_one if w.workout_type == "long_ride"]
    assert long_days == [5]


def test_override_forces_rest_on_unavailable_dates() -> None:
    skeleton = _skeleton(weeks=4)
    # Mark the first Wed-Fri of the plan unavailable (a mid-week "away" block).
    away = [START + timedelta(days=offset) for offset in (2, 3, 4)]
    overrides = [
        ScheduleOverride(user_id="athlete-1", override_date=d, available=False) for d in away
    ]
    workouts = compose_plan_workouts(
        skeleton,
        user_id="athlete-1",
        plan_id="plan-1",
        sport="cycling",
        overrides=overrides,
    )
    by_date = {w.workout_date: w for w in workouts}
    for d in away:
        assert by_date[d].workout_type == "rest", f"{d} should be forced rest"
        assert by_date[d].target_tss is None
    # A date with no override keeps its templated (non-rest) prescription somewhere in week 1.
    week_one_types = {w.workout_type for w in workouts if w.week_number == 1}
    assert week_one_types - {"rest"}, "week 1 should still contain training days"


def test_override_zero_max_hours_forces_rest_even_when_available() -> None:
    skeleton = _skeleton(weeks=2)
    baseline = compose_plan_workouts(
        skeleton, user_id="athlete-1", plan_id="plan-1", sport="cycling"
    )
    # Pick a training day (has a TSS target) in week 1.
    training_day = next(w for w in baseline if w.week_number == 1 and w.target_tss)
    override = ScheduleOverride(
        user_id="athlete-1", override_date=training_day.workout_date, available=True, max_hours=0
    )
    workouts = compose_plan_workouts(
        skeleton,
        user_id="athlete-1",
        plan_id="plan-1",
        sport="cycling",
        overrides=[override],
    )
    day = next(w for w in workouts if w.workout_date == training_day.workout_date)
    assert day.workout_type == "rest"
    assert day.target_tss is None
    assert day.target_duration_minutes is None


def test_override_max_hours_caps_target_tss() -> None:
    skeleton = _skeleton(weeks=2)
    # Find the long day (highest TSS) in week 1 and cap it hard at 1 hour.
    baseline = compose_plan_workouts(
        skeleton, user_id="athlete-1", plan_id="plan-1", sport="cycling"
    )
    week_one = [w for w in baseline if w.week_number == 1 and w.target_tss]
    long_day = max(week_one, key=lambda w: w.target_tss or 0)
    override = ScheduleOverride(
        user_id="athlete-1", override_date=long_day.workout_date, available=True, max_hours=1.0
    )
    capped = compose_plan_workouts(
        skeleton,
        user_id="athlete-1",
        plan_id="plan-1",
        sport="cycling",
        overrides=[override],
    )
    capped_day = next(w for w in capped if w.workout_date == long_day.workout_date)
    assert (capped_day.target_tss or 0) < (long_day.target_tss or 0)
    assert (capped_day.target_tss or 0) <= 1.0 * 45.0 + 0.1  # 1h * TSS_PER_HOUR_EASY


def test_from_date_emits_only_future_workouts_with_continuous_ramp() -> None:
    skeleton = _skeleton(weeks=4)
    from_date = START + timedelta(days=10)
    full = compose_plan_workouts(skeleton, user_id="athlete-1", plan_id="plan-1", sport="cycling")
    partial = compose_plan_workouts(
        skeleton,
        user_id="athlete-1",
        plan_id="plan-1",
        sport="cycling",
        from_date=from_date,
    )
    assert all(w.workout_date >= from_date for w in partial)
    # Recomposed future workouts are identical to the full plan's future slice
    # (same skeleton ⇒ continuous TSS ramp, not a reset).
    full_future = {
        w.workout_date: w.model_dump(exclude={"id"}) for w in full if w.workout_date >= from_date
    }
    partial_by_date = {w.workout_date: w.model_dump(exclude={"id"}) for w in partial}
    assert partial_by_date == full_future


def test_composition_is_deterministic() -> None:
    first = compose_plan_workouts(
        _skeleton(), user_id="athlete-1", plan_id="plan-1", sport="cycling"
    )
    second = compose_plan_workouts(
        _skeleton(), user_id="athlete-1", plan_id="plan-1", sport="cycling"
    )
    assert [w.model_dump(exclude={"id"}) for w in first] == [
        w.model_dump(exclude={"id"}) for w in second
    ]
