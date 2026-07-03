"""Plan↔activity matching and compliance summarization.

The matcher is deterministic and pure: given planned workouts and recorded
activities it proposes confident (workout, activity) pairs. Persistence of
those pairs (the "reconcile" step) lives with the callers in ``api/index.py``
so this module stays trivially unit-testable.

Semantics (product decisions):
- A past-dated workout that is still ``scheduled`` with no linked activity is
  *unconfirmed* — derived at read time, never persisted, and never auto-aged
  into skipped/missed. Only explicit athlete/coach action resolves it.
- ``rest`` workouts are excluded from matching and from the compliance
  denominator.
- The summary window runs from the plan start, capped at 28 days back.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from backend.models.training import Activity, PlanWorkout, TrainingPlan

COMPLIANCE_WINDOW_DAYS = 28
MATCH_MAX_DAY_OFFSET = 1
UNCONFIRMED_MAX_ITEMS = 3
UNCONFIRMED_MAX_AGE_DAYS = 14
UNPLANNED_MAX_ITEMS = 10

# Below this duration ratio (shorter / longer) an activity cannot confirm a
# workout: a 30-minute spin should not complete a 3-hour long ride.
_MIN_DURATION_RATIO = 0.4
# Score a candidate pair must reach to be considered a confident auto-match.
_MIN_MATCH_SCORE = 0.5

_NON_TRAINING_TYPES = frozenset({"rest"})


@dataclass(frozen=True)
class WorkoutMatch:
    workout: PlanWorkout
    activity: Activity
    score: float


def _ratio(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        return 0.0
    return min(a, b) / max(a, b)


def _pair_score(workout: PlanWorkout, activity: Activity) -> float | None:
    """Score a candidate pair, or None when the pair is disqualified."""
    if workout.sport.strip().casefold() != activity.sport.strip().casefold():
        return None

    day_offset = abs((activity.activity_date - workout.workout_date).days)
    if day_offset > MATCH_MAX_DAY_OFFSET:
        return None

    score = 1.0 - 0.25 * day_offset

    if workout.target_duration_minutes and activity.duration_seconds:
        duration_ratio = _ratio(
            float(workout.target_duration_minutes * 60), float(activity.duration_seconds)
        )
        if duration_ratio < _MIN_DURATION_RATIO:
            return None
        score -= (1.0 - duration_ratio) * 0.5

    if workout.target_tss and activity.tss:
        score -= (1.0 - _ratio(float(workout.target_tss), float(activity.tss))) * 0.25

    return score if score >= _MIN_MATCH_SCORE else None


def _matchable_workout(workout: PlanWorkout, today: date) -> bool:
    return (
        workout.status == "scheduled"
        and workout.actual_activity_id is None
        and workout.workout_type not in _NON_TRAINING_TYPES
        and workout.workout_date <= today
    )


def match_activities_to_workouts(
    planned: list[PlanWorkout],
    activities: list[Activity],
    *,
    today: date,
) -> list[WorkoutMatch]:
    """Greedy best-score 1:1 assignment of activities to open past workouts."""
    candidates: list[WorkoutMatch] = []
    for workout in planned:
        if not _matchable_workout(workout, today):
            continue
        for activity in activities:
            if activity.planned_workout_id is not None:
                continue
            score = _pair_score(workout, activity)
            if score is not None:
                candidates.append(WorkoutMatch(workout=workout, activity=activity, score=score))

    candidates.sort(
        key=lambda m: (-m.score, m.workout.workout_date, m.workout.id or "", m.activity.id or "")
    )

    matches: list[WorkoutMatch] = []
    used_workouts: set[object] = set()
    used_activities: set[object] = set()
    for candidate in candidates:
        # Fall back to object identity for unsaved rows so two records
        # without ids are never conflated into one "duplicate".
        workout_key: object = candidate.workout.id or id(candidate.workout)
        activity_key: object = candidate.activity.id or id(candidate.activity)
        if workout_key in used_workouts or activity_key in used_activities:
            continue
        used_workouts.add(workout_key)
        used_activities.add(activity_key)
        matches.append(candidate)
    return matches


def compliance_window(plan_start: date, today: date) -> tuple[date, date]:
    """Window from plan start to today, capped at COMPLIANCE_WINDOW_DAYS."""
    earliest = today - timedelta(days=COMPLIANCE_WINDOW_DAYS - 1)
    return max(plan_start, earliest), today


def _is_unconfirmed(workout: PlanWorkout, today: date) -> bool:
    return (
        workout.status == "scheduled"
        and workout.actual_activity_id is None
        and workout.workout_date < today
        and workout.workout_type not in _NON_TRAINING_TYPES
    )


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _unconfirmed_session(workout: PlanWorkout) -> dict[str, Any]:
    return {
        "id": workout.id,
        "workout_date": workout.workout_date.isoformat(),
        "sport": workout.sport,
        "title": workout.title,
        "workout_type": workout.workout_type,
        "target_duration_minutes": workout.target_duration_minutes,
    }


def _unplanned_activity(activity: Activity) -> dict[str, Any]:
    return {
        "id": activity.id,
        "activity_date": activity.activity_date.isoformat(),
        "sport": activity.sport,
        "duration_seconds": activity.duration_seconds,
        "tss": activity.tss,
        "athlete_notes": activity.athlete_notes,
    }


def _pct(completed: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(completed / denominator * 100, 1)


def build_compliance_summary(
    plan: TrainingPlan,
    planned: list[PlanWorkout],
    activities: list[Activity],
    *,
    today: date,
) -> dict[str, Any]:
    """Summarize planned-versus-done over the compliance window.

    Callers are expected to pass workouts/activities already scoped to (at
    least) the window; anything outside it is ignored here.
    """
    start, end = compliance_window(plan.start_date, today)

    # No end cap here: anything dated after `end` (today) simply lands in the
    # "upcoming" bucket, so callers may pass a few future days for context.
    in_window = [w for w in planned if start <= w.workout_date]
    trainable = [w for w in in_window if w.workout_type not in _NON_TRAINING_TYPES]
    # Today's workout is "upcoming" until the day has passed — unless it is
    # already resolved (it can still auto-complete via matching, but must not
    # read as unconfirmed yet). Future-dated workouts never enter the
    # compliance buckets regardless of status.
    past = [
        w
        for w in trainable
        if w.workout_date < today or (w.workout_date == today and w.status != "scheduled")
    ]
    past_keys = {id(w) for w in past}
    upcoming = [w for w in trainable if id(w) not in past_keys]

    completed = [w for w in past if w.status in ("completed", "modified")]
    skipped = [w for w in past if w.status == "skipped"]
    unconfirmed = sorted(
        (w for w in past if _is_unconfirmed(w, today)),
        key=lambda w: w.workout_date,
        reverse=True,
    )

    nudge_cutoff = today - timedelta(days=UNCONFIRMED_MAX_AGE_DAYS)
    nudgeable = [w for w in unconfirmed if w.workout_date >= nudge_cutoff]

    weeks: list[dict[str, Any]] = []
    for week_start in sorted({_week_start(w.workout_date) for w in past}):
        week_end = week_start + timedelta(days=6)
        week_past = [w for w in past if week_start <= w.workout_date <= week_end]
        week_completed = sum(1 for w in week_past if w.status in ("completed", "modified"))
        weeks.append(
            {
                "start": week_start.isoformat(),
                "end": week_end.isoformat(),
                "planned": len(week_past),
                "completed": week_completed,
                "skipped": sum(1 for w in week_past if w.status == "skipped"),
                "unconfirmed": sum(1 for w in week_past if _is_unconfirmed(w, today)),
                "compliance_pct": _pct(week_completed, len(week_past)),
            }
        )

    unplanned = [
        a for a in activities if a.planned_workout_id is None and start <= a.activity_date <= end
    ]

    return {
        "status": "ok",
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "totals": {
            "planned": len(past),
            "completed": len(completed),
            "skipped": len(skipped),
            "unconfirmed": len(unconfirmed),
            "upcoming": len(upcoming),
            "unplanned_activities": len(unplanned),
        },
        "compliance_pct": _pct(len(completed), len(past)),
        "weeks": weeks,
        "unconfirmed_sessions": [
            _unconfirmed_session(w) for w in nudgeable[:UNCONFIRMED_MAX_ITEMS]
        ],
        "unplanned_activities": [_unplanned_activity(a) for a in unplanned[:UNPLANNED_MAX_ITEMS]],
    }
