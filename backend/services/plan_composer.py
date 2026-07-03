"""Deterministic daily workout composition from a periodized plan skeleton.

Turns each week of a ``PlanSkeleton`` (one ``PhasePlan`` per week with a
weekly TSS target and intensity guidance) into seven ``PlanWorkout`` rows
using a simple weekly template:

- unavailable days (athlete schedule) are rest days; every week keeps at
  least one rest day even when fully available
- one long session anchors the weekend (~30% of weekly TSS)
- up to ``max_hiit_per_week`` quality sessions land mid-week (~20% each),
  with the type chosen by the phase focus
- remaining available days split the leftover TSS as endurance rides/runs

The output is intentionally boring and reproducible: same inputs, same plan.
"""

from datetime import timedelta

from backend.engine.periodization import PhasePlan, PlanSkeleton
from backend.models.training import PlanWorkout

_DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_DAYS_PER_WEEK = 7

# Weekly TSS shares.
_LONG_SHARE = 0.30
_QUALITY_SHARE = 0.20

# Rough TSS-per-hour by session intensity, used to derive target durations.
_TSS_PER_HOUR_EASY = 45.0
_TSS_PER_HOUR_QUALITY = 75.0

# Preferred slots (weekday indexes, Monday=0).
_LONG_DAY_PREFERENCE = (5, 6, 4)  # Saturday, Sunday, Friday
_QUALITY_DAY_PREFERENCE = (1, 3, 4)  # Tuesday, Thursday, Friday
_REST_DAY_PREFERENCE = (0, 4, 2)  # Monday, Friday, Wednesday

_QUALITY_TYPE_BY_FOCUS = {
    "base": "tempo",
    "build": "threshold",
    "peak": "vo2max",
    "taper": "threshold",
}

_LONG_TYPE_BY_SPORT = {
    "cycling": "long_ride",
    "running": "long_run",
}


def _available_days(weekly_pattern: dict | None) -> set[int]:
    if not weekly_pattern:
        return set(range(7))
    days = set(range(7))
    for index, name in enumerate(_DAY_NAMES):
        entry = weekly_pattern.get(name)
        if isinstance(entry, dict) and entry.get("available") is False:
            days.discard(index)
    return days


def _pick(preference: tuple[int, ...], pool: set[int]) -> int | None:
    for day in preference:
        if day in pool:
            return day
    return min(pool) if pool else None


def _duration_minutes(tss: float, tss_per_hour: float) -> int:
    return max(20, round(tss / tss_per_hour * 60))


def _session(workout_type: str, title: str, tss: float, tss_per_hour: float) -> dict:
    return {
        "workout_type": workout_type,
        "title": title,
        "target_tss": round(tss, 1),
        "target_duration_minutes": _duration_minutes(tss, tss_per_hour),
    }


def _assign_long_day(
    sessions: dict[int, dict], open_days: set[int], weekly_tss: float, sport: str
) -> float:
    """Place the long session; returns the TSS it consumed."""
    long_day = _pick(_LONG_DAY_PREFERENCE, open_days)
    if long_day is None:
        return 0.0
    open_days.discard(long_day)
    long_tss = weekly_tss * _LONG_SHARE
    long_type = _LONG_TYPE_BY_SPORT.get(sport, "endurance")
    sessions[long_day] = _session(long_type, f"Long {sport} session", long_tss, _TSS_PER_HOUR_EASY)
    return long_tss


def _assign_quality_days(sessions: dict[int, dict], open_days: set[int], phase: PhasePlan) -> float:
    """Place quality sessions per phase guidance; returns the TSS they consumed."""
    if phase.focus == "recovery":
        return 0.0
    quality_type = _QUALITY_TYPE_BY_FOCUS.get(phase.focus, "tempo")
    consumed = 0.0
    for _ in range(min(phase.max_hiit_per_week, len(_QUALITY_DAY_PREFERENCE))):
        quality_day = _pick(_QUALITY_DAY_PREFERENCE, open_days)
        if quality_day is None:
            break
        open_days.discard(quality_day)
        quality_tss = phase.target_weekly_tss * _QUALITY_SHARE
        consumed += quality_tss
        sessions[quality_day] = _session(
            quality_type,
            f"{phase.name} {quality_type.replace('_', ' ')} intervals",
            quality_tss,
            _TSS_PER_HOUR_QUALITY,
        )
    return consumed


def _plan_week(phase: PhasePlan, sport: str, available: set[int]) -> dict[int, dict]:
    """Assign a session template to each weekday index of one week."""
    weekly_tss = phase.target_weekly_tss
    sessions: dict[int, dict] = {}
    open_days = set(available)

    # Rest: all unavailable days, plus one guaranteed rest day.
    if len(open_days) == _DAYS_PER_WEEK:
        rest_day = _pick(_REST_DAY_PREFERENCE, open_days)
        if rest_day is not None:
            open_days.discard(rest_day)

    consumed_tss = _assign_long_day(sessions, open_days, weekly_tss, sport)
    consumed_tss += _assign_quality_days(sessions, open_days, phase)

    remaining_tss = max(0.0, weekly_tss - consumed_tss)
    easy_type = "recovery" if phase.focus == "recovery" else "endurance"
    easy_days = sorted(open_days)
    for day in easy_days:
        sessions[day] = _session(
            easy_type,
            f"{easy_type.capitalize()} {sport}",
            remaining_tss / len(easy_days),
            _TSS_PER_HOUR_EASY,
        )
    if not easy_days and remaining_tss > 0 and consumed_tss > 0:
        # Availability left no room for easy days: scale the placed sessions
        # up so the week still sums to the weekly TSS target.
        factor = weekly_tss / consumed_tss
        for session in sessions.values():
            session["target_tss"] = round(session["target_tss"] * factor, 1)
            session["target_duration_minutes"] = round(session["target_duration_minutes"] * factor)

    for day in range(_DAYS_PER_WEEK):
        if day not in sessions:
            sessions[day] = {
                "workout_type": "rest",
                "title": "Rest day",
                "target_tss": None,
                "target_duration_minutes": None,
            }
    return sessions


def compose_plan_workouts(
    skeleton: PlanSkeleton,
    *,
    user_id: str,
    plan_id: str,
    sport: str,
    weekly_pattern: dict | None = None,
) -> list[PlanWorkout]:
    """Compose one PlanWorkout per calendar day of the skeleton."""
    available = _available_days(weekly_pattern)
    phase_by_week = {
        week: phase
        for phase in skeleton.phases
        for week in range(phase.start_week, phase.end_week + 1)
    }

    workouts: list[PlanWorkout] = []
    for week_number in range(1, skeleton.total_weeks + 1):
        phase = phase_by_week.get(week_number)
        if phase is None:
            raise ValueError(
                f"Plan skeleton has no phase covering week {week_number}; "
                "refusing to persist an incomplete plan."
            )
        week_start = skeleton.start_date + timedelta(weeks=week_number - 1)
        sessions = _plan_week(phase, sport, available)
        for day_index in range(_DAYS_PER_WEEK):
            workout_date = week_start + timedelta(days=day_index)
            # Sessions are keyed by real weekday (Monday=0) so schedule
            # availability and slot preferences hold for any plan start day.
            session = sessions[workout_date.weekday()]
            workouts.append(
                PlanWorkout(
                    plan_id=plan_id,
                    user_id=user_id,
                    workout_date=workout_date,
                    day_of_week=workout_date.weekday(),
                    week_number=week_number,
                    phase_name=phase.name,
                    sport=sport,
                    title=session["title"],
                    description=phase.description,
                    workout_type=session["workout_type"],
                    target_duration_minutes=session["target_duration_minutes"],
                    target_tss=session["target_tss"],
                )
            )
    return workouts
