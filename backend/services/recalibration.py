"""Threshold recalibration from recent performance evidence.

Semantics (product decisions):
- Evidence must reflect a maximal/hard effort, not routine training. An
  activity qualifies when its RPE is explicitly high, or — absent an RPE —
  its pace/power already meets or beats the athlete's current LT2. Without
  either signal there is nothing to disqualify a routine session with, so it
  is excluded rather than risk a deflated threshold.
- When multiple activities qualify in the lookback window, the best
  performance wins, not the most recent one: a single off day shouldn't drag
  down a durable capability estimate when a better qualifying effort exists
  in the same window.
- Sports without a deterministic estimator (anything but running/cycling)
  report insufficient_evidence rather than erroring.
- A current threshold the athlete has manually confirmed (source == "user")
  is never superseded here; the caller must not persist over it.
- This module is pure: activities and the current threshold are passed in as
  data, never fetched. Persistence is the caller's job (mirrors
  backend/services/compliance.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from backend.engine.thresholds import (
    EIGHT_MINUTE_TEST_MINUTES,
    FULL_FTP_TEST_MINUTES,
    TWELVE_MINUTE_TEST_MINUTES,
    TWENTY_MINUTE_TEST_MINUTES,
    estimate_cycling_thresholds,
    estimate_running_thresholds,
)
from backend.engine.zones import compute_zones
from backend.models.athlete import SportThreshold
from backend.models.training import Activity

RECALIBRATION_LOOKBACK_DAYS = 90
LOW_CONFIDENCE_REASK_DAYS = 7
MEDIUM_HIGH_CONFIDENCE_REASK_DAYS = 28

_MIN_HARD_EFFORT_RPE = 8
_HIGH_CONFIDENCE_RPE = 9

_MIN_RUNNING_RACE_METERS = 3000
_MAX_RUNNING_RACE_METERS = 42_500

_TEST_WINDOW_TOLERANCE_MINUTES = 3
_COGGAN_TEST_WINDOWS_MINUTES = (
    EIGHT_MINUTE_TEST_MINUTES,
    TWELVE_MINUTE_TEST_MINUTES,
    TWENTY_MINUTE_TEST_MINUTES,
    FULL_FTP_TEST_MINUTES,
)

_PACE_EPSILON_SEC_KM = 1
_POWER_EPSILON_WATTS = 1

RecalibrationStatus = Literal[
    "recalibrated", "insufficient_evidence", "already_user_confirmed", "no_change"
]

ESTIMABLE_SPORTS = frozenset({"running", "cycling"})


@dataclass(frozen=True)
class RecalibrationResult:
    sport: str
    status: RecalibrationStatus
    evidence_activity_id: str | None
    evidence_reason: str | None
    candidate: SportThreshold | None  # unsaved; caller persists via the repo
    confidence: str | None
    explanation: str


def recalibration_cadence_days(confidence: Literal["low", "medium", "high"]) -> int:
    """Minimum days before proposing another candidate at this confidence tier."""
    return LOW_CONFIDENCE_REASK_DAYS if confidence == "low" else MEDIUM_HIGH_CONFIDENCE_REASK_DAYS


def next_recalibration_eligible_date(
    confidence: Literal["low", "medium", "high"], generated_at: date
) -> date:
    return generated_at + timedelta(days=recalibration_cadence_days(confidence))


def recalibration_cadence_gate(
    confidence: Literal["low", "medium", "high"],
    last_generated_at: date | None,
    *,
    today: date | None = None,
) -> date | None:
    """Return next eligible date when cadence blocks a proposal; otherwise None."""
    if last_generated_at is None:
        return None
    today = today or date.today()
    next_eligible = next_recalibration_eligible_date(confidence, last_generated_at)
    return next_eligible if today < next_eligible else None


def _classify_effort(rpe: int | None, meets_or_beats_current: bool) -> str | None:
    """Confidence tier for a candidate effort, or None if it's not hard evidence."""
    if rpe is not None:
        if rpe >= _HIGH_CONFIDENCE_RPE:
            return "high"
        if rpe >= _MIN_HARD_EFFORT_RPE:
            return "medium"
        return None
    return "high" if meets_or_beats_current else None


def _running_confidence(activity: Activity, current: SportThreshold | None) -> str | None:
    if not activity.distance_meters or not activity.duration_seconds:
        return None
    if not (_MIN_RUNNING_RACE_METERS <= activity.distance_meters <= _MAX_RUNNING_RACE_METERS):
        return None
    meets_current = (
        current is not None
        and current.lt2_pace_sec_per_km is not None
        and (activity.duration_seconds / (activity.distance_meters / 1000))
        <= current.lt2_pace_sec_per_km
    )
    return _classify_effort(activity.rpe, meets_current)


def _cycling_confidence(activity: Activity, current: SportThreshold | None) -> str | None:
    power = activity.avg_power_watts or activity.normalized_power_watts
    if not power or not activity.duration_seconds:
        return None
    duration_minutes = activity.duration_seconds / 60
    if not any(
        abs(duration_minutes - window) <= _TEST_WINDOW_TOLERANCE_MINUTES
        for window in _COGGAN_TEST_WINDOWS_MINUTES
    ):
        return None
    meets_current = (
        current is not None
        and current.lt2_power_watts is not None
        and power >= current.lt2_power_watts
    )
    return _classify_effort(activity.rpe, meets_current)


def _running_candidate_pace(activity: Activity) -> int:
    """Lower is better: the LT2 pace this effort implies."""
    duration = activity.duration_seconds or 0
    distance = int(activity.distance_meters or 0)
    return estimate_running_thresholds(duration, distance).lt2_pace_sec_km


def _cycling_candidate_ftp(activity: Activity) -> int:
    """Higher is better: the FTP this effort implies."""
    power = int(activity.avg_power_watts or activity.normalized_power_watts or 0)
    duration_minutes = (activity.duration_seconds or 0) / 60
    result = estimate_cycling_thresholds(power, round(duration_minutes))
    return result.ftp_watts


def _pick_winner(sport: str, eligible: list[tuple[Activity, str]]) -> tuple[Activity, str] | None:
    """Best-performance-wins; ties broken by most recent activity_date."""
    if not eligible:
        return None

    if sport == "running":
        # Lower implied LT2 pace = faster = better.
        return min(
            eligible,
            key=lambda pair: (_running_candidate_pace(pair[0]), -pair[0].activity_date.toordinal()),
        )
    # Cycling: higher implied FTP = better.
    return max(
        eligible,
        key=lambda pair: (_cycling_candidate_ftp(pair[0]), pair[0].activity_date.toordinal()),
    )


def _build_candidate(  # noqa: PLR0913
    sport: str,
    activity: Activity,
    confidence: str,
    current: SportThreshold | None,
    user_id: str,
    today: date,
) -> tuple[SportThreshold, str]:
    """Returns the unsaved candidate plus a human-readable explanation of the delta."""
    base = (
        current.model_copy(update={"id": None, "superseded_at": None})
        if current is not None
        else SportThreshold(user_id=user_id, sport=sport)
    )

    updates: dict[str, object]
    if sport == "running":
        duration = activity.duration_seconds or 0
        distance = int(activity.distance_meters or 0)
        result = estimate_running_thresholds(duration, distance)
        old_pace = current.lt2_pace_sec_per_km if current else None
        updates = {
            "lt2_pace_sec_per_km": result.lt2_pace_sec_km,
            "lt1_pace_sec_per_km": result.lt1_pace_sec_km,
        }
        delta = (
            f"LT2 pace {old_pace}s/km -> {result.lt2_pace_sec_km}s/km"
            if old_pace is not None
            else f"LT2 pace estimated at {result.lt2_pace_sec_km}s/km"
        )
        method = "race_time"
    else:
        power = int(activity.avg_power_watts or activity.normalized_power_watts or 0)
        duration_minutes = (activity.duration_seconds or 0) / 60
        result = estimate_cycling_thresholds(power, round(duration_minutes))
        old_ftp = current.lt2_power_watts if current else None
        updates = {
            "lt2_power_watts": result.ftp_watts,
            "lt1_power_watts": result.lt1_watts,
        }
        delta = (
            f"FTP {old_ftp}W -> {result.ftp_watts}W"
            if old_ftp is not None
            else f"FTP estimated at {result.ftp_watts}W"
        )
        method = "field_test"

    updates["estimation_method"] = method
    updates["estimation_source"] = f"activity:{activity.id}:{activity.activity_date.isoformat()}"
    updates["confidence"] = confidence
    updates["source"] = "file"
    updates["effective_from"] = today
    candidate = base.model_copy(update=updates)
    candidate.zones = [
        z.to_dict()
        for z in compute_zones(
            sport,
            ftp_watts=candidate.lt2_power_watts,
            lt1_power_watts=candidate.lt1_power_watts,
            lt2_pace_sec_km=candidate.lt2_pace_sec_per_km,
            lt1_pace_sec_km=candidate.lt1_pace_sec_per_km,
            max_hr=None,
            lt2_hr=candidate.lt2_hr_bpm,
            lt1_hr=candidate.lt1_hr_bpm,
        )
    ]

    explanation = f"{delta} from an activity on {activity.activity_date.isoformat()}" + (
        f" (rpe {activity.rpe})" if activity.rpe is not None else ""
    )
    return candidate, explanation


def _is_unchanged(sport: str, candidate: SportThreshold, current: SportThreshold | None) -> bool:
    if current is None:
        return False
    if sport == "running":
        if current.lt2_pace_sec_per_km is None or current.lt1_pace_sec_per_km is None:
            return False
        lt2_delta = abs((candidate.lt2_pace_sec_per_km or 0) - current.lt2_pace_sec_per_km)
        lt1_delta = abs((candidate.lt1_pace_sec_per_km or 0) - current.lt1_pace_sec_per_km)
        return lt2_delta <= _PACE_EPSILON_SEC_KM and lt1_delta <= _PACE_EPSILON_SEC_KM
    if current.lt2_power_watts is None or current.lt1_power_watts is None:
        return False
    lt2_delta = abs((candidate.lt2_power_watts or 0) - current.lt2_power_watts)
    lt1_delta = abs((candidate.lt1_power_watts or 0) - current.lt1_power_watts)
    return lt2_delta <= _POWER_EPSILON_WATTS and lt1_delta <= _POWER_EPSILON_WATTS


def evaluate_recalibration(
    sport: str,
    activities: list[Activity],
    current: SportThreshold | None,
    user_id: str,
    *,
    today: date | None = None,
) -> RecalibrationResult:
    today = today or date.today()

    if sport not in ESTIMABLE_SPORTS:
        return RecalibrationResult(
            sport=sport,
            status="insufficient_evidence",
            evidence_activity_id=None,
            evidence_reason=None,
            candidate=None,
            confidence=None,
            explanation=f"No deterministic threshold estimator is available for sport '{sport}'.",
        )

    if current is not None and current.derived_source == "user":
        return RecalibrationResult(
            sport=sport,
            status="already_user_confirmed",
            evidence_activity_id=None,
            evidence_reason=None,
            candidate=None,
            confidence=None,
            explanation=(
                "Current threshold was manually confirmed by the athlete; "
                "recalibration will not override it."
            ),
        )

    since = today - timedelta(days=RECALIBRATION_LOOKBACK_DAYS)
    classify = _running_confidence if sport == "running" else _cycling_confidence
    eligible: list[tuple[Activity, str]] = []
    for activity in activities:
        if activity.sport.strip().casefold() != sport or activity.activity_date < since:
            continue
        confidence = classify(activity, current)
        if confidence is not None:
            eligible.append((activity, confidence))

    winner = _pick_winner(sport, eligible)
    if winner is None:
        return RecalibrationResult(
            sport=sport,
            status="insufficient_evidence",
            evidence_activity_id=None,
            evidence_reason=None,
            candidate=None,
            confidence=None,
            explanation=(
                f"No recent {sport} activity in the last {RECALIBRATION_LOOKBACK_DAYS} days "
                "looks like a hard enough effort to recalibrate from."
            ),
        )

    activity, confidence = winner
    candidate, explanation = _build_candidate(sport, activity, confidence, current, user_id, today)

    if _is_unchanged(sport, candidate, current):
        return RecalibrationResult(
            sport=sport,
            status="no_change",
            evidence_activity_id=activity.id,
            evidence_reason=explanation,
            candidate=None,
            confidence=confidence,
            explanation=(
                "Best recent evidence matches the current threshold; "
                f"no update needed. {explanation}"
            ),
        )

    return RecalibrationResult(
        sport=sport,
        status="recalibrated",
        evidence_activity_id=activity.id,
        evidence_reason=explanation,
        candidate=candidate,
        confidence=confidence,
        explanation=explanation,
    )


def evaluate_all(
    activities_by_sport: dict[str, list[Activity]],
    current_by_sport: dict[str, SportThreshold],
    user_id: str,
    *,
    today: date | None = None,
) -> list[RecalibrationResult]:
    sports = sorted(set(activities_by_sport) | set(current_by_sport))
    return [
        evaluate_recalibration(
            sport,
            activities_by_sport.get(sport, []),
            current_by_sport.get(sport),
            user_id,
            today=today,
        )
        for sport in sports
    ]
