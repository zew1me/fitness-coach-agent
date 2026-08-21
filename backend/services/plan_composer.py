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

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from backend.engine.periodization import PhasePlan, PlanSkeleton
from backend.models.athlete import ScheduleOverride
from backend.models.training import PlanWorkout

logger = logging.getLogger(__name__)

TrainingModel = Literal["performance", "longevity", "recovery_return"]

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


@dataclass(frozen=True)
class PlanComposerPolicy:
    training_model: TrainingModel = "performance"

    @property
    def long_share(self) -> float:
        if self.training_model == "recovery_return":
            return 0.20
        if self.training_model == "longevity":
            return 0.25
        return _LONG_SHARE

    @property
    def quality_share(self) -> float:
        if self.training_model == "recovery_return":
            return 0.0
        if self.training_model == "longevity":
            return 0.12
        return _QUALITY_SHARE

    @property
    def max_quality_sessions(self) -> int | None:
        if self.training_model == "recovery_return":
            return 0
        if self.training_model == "longevity":
            return 1
        return None

    @property
    def minimum_rest_days(self) -> int:
        return 2 if self.training_model == "recovery_return" else 1


def _available_days(weekly_pattern: dict | None) -> set[int]:
    if not weekly_pattern:
        return set(range(7))
    days = set(range(7))
    for index, name in enumerate(_DAY_NAMES):
        entry = weekly_pattern.get(name)
        if isinstance(entry, dict) and entry.get("available") is False:
            days.discard(index)
    return days


def _rest_session() -> dict:
    return {
        "workout_type": "rest",
        "title": "Rest day",
        "target_tss": None,
        "target_duration_minutes": None,
    }


def _apply_override(session: dict, override: ScheduleOverride) -> dict:
    """Layer a dated schedule override onto a weekly-template session (issue #232).

    ``available=False`` forces a rest day on that exact date; a ``max_hours`` cap
    scales the day's target TSS/duration down proportionally when the templated
    session would exceed the athlete's stated availability for that date.
    """
    if not override.available:
        return _rest_session()
    # Zero available hours is a rest day even when the athlete is nominally
    # "available" — otherwise the templated session survives at 0 min/0 TSS.
    if override.max_hours is not None and override.max_hours <= 0:
        return _rest_session()
    target_tss = session["target_tss"]
    if override.max_hours is not None and target_tss is not None:
        cap_tss = override.max_hours * _TSS_PER_HOUR_EASY
        if target_tss > cap_tss:
            scale = cap_tss / target_tss if target_tss else 0.0
            duration = session["target_duration_minutes"] or 0
            return {
                **session,
                "target_tss": round(cap_tss, 1),
                "target_duration_minutes": round(duration * scale),
            }
    return session


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
    sessions: dict[int, dict],
    open_days: set[int],
    weekly_tss: float,
    sport: str,
    policy: PlanComposerPolicy,
) -> float:
    """Place the long session; returns the TSS it consumed."""
    long_day = _pick(_LONG_DAY_PREFERENCE, open_days)
    if long_day is None:
        return 0.0
    open_days.discard(long_day)
    long_tss = weekly_tss * policy.long_share
    long_type = _LONG_TYPE_BY_SPORT.get(sport, "endurance")
    sessions[long_day] = _session(long_type, f"Long {sport} session", long_tss, _TSS_PER_HOUR_EASY)
    return long_tss


def _assign_quality_days(
    sessions: dict[int, dict],
    open_days: set[int],
    phase: PhasePlan,
    policy: PlanComposerPolicy,
) -> float:
    """Place quality sessions per phase guidance; returns the TSS they consumed."""
    if phase.focus == "recovery":
        return 0.0
    max_quality = phase.max_hiit_per_week
    if policy.max_quality_sessions is not None:
        max_quality = min(max_quality, policy.max_quality_sessions)
    if max_quality <= 0:
        return 0.0
    if max_quality > len(_QUALITY_DAY_PREFERENCE):
        logger.warning(
            "Phase %r requested %d quality sessions, but only %d preferred quality "
            "day slots exist; the request will be truncated.",
            phase.name,
            max_quality,
            len(_QUALITY_DAY_PREFERENCE),
        )
    quality_type = (
        "tempo"
        if policy.training_model == "longevity"
        else _QUALITY_TYPE_BY_FOCUS.get(phase.focus, "tempo")
    )
    consumed = 0.0
    for _ in range(min(max_quality, len(_QUALITY_DAY_PREFERENCE))):
        quality_day = _pick(_QUALITY_DAY_PREFERENCE, open_days)
        if quality_day is None:
            break
        open_days.discard(quality_day)
        quality_tss = phase.target_weekly_tss * policy.quality_share
        consumed += quality_tss
        sessions[quality_day] = _session(
            quality_type,
            f"{phase.name} {quality_type.replace('_', ' ')} intervals",
            quality_tss,
            _TSS_PER_HOUR_QUALITY,
        )
    return consumed


def _plan_week(
    phase: PhasePlan, sport: str, available: set[int], policy: PlanComposerPolicy
) -> dict[int, dict]:
    """Assign a session template to each weekday index of one week."""
    weekly_tss = phase.target_weekly_tss
    sessions: dict[int, dict] = {}
    open_days = set(available)

    # Rest: all unavailable days, plus enough guaranteed rest days to satisfy
    # the selected training policy.
    unavailable_count = _DAYS_PER_WEEK - len(open_days)
    rest_days_needed = max(0, policy.minimum_rest_days - unavailable_count)
    for _ in range(rest_days_needed):
        rest_day = _pick(_REST_DAY_PREFERENCE, open_days)
        if rest_day is not None:
            open_days.discard(rest_day)

    consumed_tss = _assign_long_day(sessions, open_days, weekly_tss, sport, policy)
    consumed_tss += _assign_quality_days(sessions, open_days, phase, policy)

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


def compose_plan_workouts(  # noqa: PLR0913
    skeleton: PlanSkeleton,
    *,
    user_id: str,
    plan_id: str,
    sport: str,
    weekly_pattern: dict | None = None,
    overrides: list[ScheduleOverride] | None = None,
    policy: PlanComposerPolicy | None = None,
    from_date: date | None = None,
) -> list[PlanWorkout]:
    """Compose one PlanWorkout per calendar day of the skeleton.

    ``weekly_pattern`` shapes the recurring weekday template; ``overrides`` layer
    dated availability on top (issue #232). ``from_date`` restricts the emitted
    workouts to that date onward while still ramping TSS from the skeleton's true
    week 1, so adjusting future weeks stays continuous with periodization.
    """
    policy = policy or PlanComposerPolicy()
    available = _available_days(weekly_pattern)
    override_by_date = {ov.override_date: ov for ov in (overrides or [])}
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
        sessions = _plan_week(phase, sport, available, policy)
        for day_index in range(_DAYS_PER_WEEK):
            workout_date = week_start + timedelta(days=day_index)
            if from_date is not None and workout_date < from_date:
                continue
            # Sessions are keyed by real weekday (Monday=0) so schedule
            # availability and slot preferences hold for any plan start day.
            session = sessions[workout_date.weekday()]
            override = override_by_date.get(workout_date)
            if override is not None:
                session = _apply_override(session, override)
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
