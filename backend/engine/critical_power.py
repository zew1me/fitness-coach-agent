"""Critical power (CP) and W' from maximal efforts (issue #250).

The hyperbolic power-duration model P(t) = CP + W'/t is linear in work:

    work(t) = P(t) * t = CP * t + W'

so an ordinary least-squares fit of work on duration gives CP as the slope and W' as
the intercept. It needs no iterative solver and no numerical dependency.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import fmean, linear_regression
from typing import Literal

from backend.engine.thresholds import cycling_thresholds_from_ftp

MIN_EFFORTS = 2
MIN_FIT_DURATION_SECONDS = 3 * 60
MAX_FIT_DURATION_SECONDS = 20 * 60
# Efforts this close together leave the slope (CP) dominated by measurement noise.
MIN_DURATION_SPREAD_SECONDS = 2 * 60

# Far above the roughly 5-40 kJ seen in trained cyclists. A larger intercept almost
# always means the longer effort was not actually maximal, which would deflate CP.
MAX_PLAUSIBLE_W_PRIME_JOULES = 60_000
# A smaller intercept means power barely fell between durations, so the shorter effort
# was not maximal. The floors sit well below any real athlete, including novices and
# people returning from injury, so they only catch fits that are not measurements.
MIN_PLAUSIBLE_W_PRIME_JOULES = 1_000
MIN_PLAUSIBLE_CP_WATTS = 30

# FTP is CP * 0.95-1.05 depending on athlete type (issue #250). Nothing here identifies
# the athlete type, so use the midpoint rather than bias every athlete in one direction.
FTP_FROM_CP_FACTOR = 1.0

_RESIDUAL_DEGREES_LOST = 2
# Commonly used acceptance limits for a CP fit: standard error within 5% of CP and 10% of W'.
_MAX_CP_RELATIVE_ERROR = 0.05
_MAX_W_PRIME_RELATIVE_ERROR = 0.10
# Long efforts pin the asymptote (CP) and a wide spread separates it from W'.
_HIGH_CONFIDENCE_LONGEST_SECONDS = 12 * 60
_HIGH_CONFIDENCE_SPREAD_SECONDS = 5 * 60


@dataclass(frozen=True)
class MaxEffort:
    duration_seconds: int
    power_watts: float


@dataclass(frozen=True)
class CriticalPowerFit:
    cp_watts: int
    w_prime_joules: int
    # None when two efforts leave no residuals to estimate error from.
    cp_standard_error_watts: float | None
    w_prime_standard_error_joules: float | None
    confidence: Literal["low", "medium", "high"]
    ftp_watts: int
    lt1_watts: int


def fit_critical_power(efforts: list[MaxEffort]) -> CriticalPowerFit:
    """Fit CP and W' from maximal efforts of 3-20 minutes.

    Raises ValueError with an athlete-explainable reason when the efforts cannot support
    a fit; a fabricated threshold would be worse than none.
    """
    _validate_efforts(efforts)

    # The curve is best power per duration, so a lesser repeat is not a maximal effort.
    best_power: dict[int, float] = {}
    for effort in efforts:
        best_power[effort.duration_seconds] = max(
            best_power.get(effort.duration_seconds, 0.0), effort.power_watts
        )
    durations = [float(duration) for duration in best_power]
    work = [power * duration for duration, power in best_power.items()]
    cp, w_prime = linear_regression(durations, work)

    if (
        cp < MIN_PLAUSIBLE_CP_WATTS
        or not MIN_PLAUSIBLE_W_PRIME_JOULES <= w_prime <= MAX_PLAUSIBLE_W_PRIME_JOULES
    ):
        raise ValueError(
            "These efforts do not fit a power-duration curve, so at least one was probably "
            "not maximal. Use each duration's true best effort."
        )

    standard_errors = _standard_errors(durations, work, cp, w_prime)
    thresholds = cycling_thresholds_from_ftp(round(cp * FTP_FROM_CP_FACTOR))
    return CriticalPowerFit(
        cp_watts=round(cp),
        w_prime_joules=round(w_prime),
        cp_standard_error_watts=round(standard_errors[0], 1) if standard_errors else None,
        w_prime_standard_error_joules=round(standard_errors[1], 1) if standard_errors else None,
        confidence=_confidence(durations, cp, w_prime, standard_errors),
        ftp_watts=thresholds.ftp_watts,
        lt1_watts=thresholds.lt1_watts,
    )


def _validate_efforts(efforts: list[MaxEffort]) -> None:
    if len(efforts) < MIN_EFFORTS:
        raise ValueError(f"A critical power fit needs at least {MIN_EFFORTS} maximal efforts.")
    for effort in efforts:
        if not MIN_FIT_DURATION_SECONDS <= effort.duration_seconds <= MAX_FIT_DURATION_SECONDS:
            raise ValueError("Critical power efforts must last between 3 and 20 minutes.")
        if effort.power_watts <= 0:
            raise ValueError("Critical power efforts must have positive power.")

    durations = [effort.duration_seconds for effort in efforts]
    if max(durations) - min(durations) < MIN_DURATION_SPREAD_SECONDS:
        raise ValueError("Critical power efforts must include durations at least 2 minutes apart.")


def _standard_errors(
    durations: list[float], work: list[float], cp: float, w_prime: float
) -> tuple[float, float] | None:
    """Standard errors of (CP, W'), or None when no residual degrees of freedom remain."""
    residual_degrees = len(durations) - _RESIDUAL_DEGREES_LOST
    if residual_degrees < 1:
        return None

    mean_duration = fmean(durations)
    duration_variation = sum((duration - mean_duration) ** 2 for duration in durations)
    residual_variance = (
        sum(
            (observed - (cp * duration + w_prime)) ** 2
            for duration, observed in zip(durations, work, strict=True)
        )
        / residual_degrees
    )
    return (
        math.sqrt(residual_variance / duration_variation),
        math.sqrt(residual_variance * (1 / len(durations) + mean_duration**2 / duration_variation)),
    )


def _separated_duration_count(durations: list[float]) -> int:
    """Count durations at least the minimum spread apart, ignoring near-identical ones."""
    count = 0
    previous = -math.inf
    for duration in sorted(durations):
        if duration - previous >= MIN_DURATION_SPREAD_SECONDS:
            count += 1
            previous = duration
    return count


def _confidence(
    durations: list[float],
    cp: float,
    w_prime: float,
    standard_errors: tuple[float, float] | None,
) -> Literal["low", "medium", "high"]:
    # Two efforts always fit perfectly, so nothing cross-checks a non-maximal one. Efforts
    # seconds apart are effectively one point, so they don't provide that cross-check either.
    if standard_errors is None or _separated_duration_count(durations) <= MIN_EFFORTS:
        return "low"
    cp_error, w_prime_error = standard_errors
    if (
        cp_error > cp * _MAX_CP_RELATIVE_ERROR
        or w_prime_error > w_prime * _MAX_W_PRIME_RELATIVE_ERROR
    ):
        return "low"
    if (
        max(durations) < _HIGH_CONFIDENCE_LONGEST_SECONDS
        or max(durations) - min(durations) < _HIGH_CONFIDENCE_SPREAD_SECONDS
    ):
        return "medium"
    return "high"
