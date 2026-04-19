"""HRV metrics derived from beat-to-beat RR intervals."""

from __future__ import annotations

import math
from itertools import pairwise
from statistics import mean, stdev
from typing import TypedDict

MIN_USABLE_RR_INTERVALS = 60
MIN_DFA_RR_INTERVALS = 64
MIN_RR_INTERVAL_MS = 300
MAX_RR_INTERVAL_MS = 2000
SECONDS_TO_MS_THRESHOLD = 10
MIN_SUCCESSIVE_INTERVALS = 2


class HRVSummary(TypedDict, total=True):
    sample_count: int
    quality: str
    artifact_pct: float
    rmssd_ms: float | None
    sdnn_ms: float | None
    dfa_alpha1: float | None


def summarize_hrv(rr_intervals_ms: list[int]) -> HRVSummary:
    """Summarize RR intervals into common time-domain metrics and DFA alpha1."""
    cleaned = [_normalize_rr_interval(value) for value in rr_intervals_ms]
    cleaned = [value for value in cleaned if value is not None]
    sample_count = len(cleaned)
    artifact_pct = (
        round(100 * (len(rr_intervals_ms) - sample_count) / len(rr_intervals_ms), 1)
        if rr_intervals_ms
        else 0.0
    )

    return {
        "sample_count": sample_count,
        "quality": _quality(sample_count),
        "artifact_pct": artifact_pct,
        "rmssd_ms": _rmssd(cleaned),
        "sdnn_ms": round(stdev(cleaned), 1) if sample_count > 1 else None,
        "dfa_alpha1": _dfa_alpha1(cleaned) if sample_count >= MIN_DFA_RR_INTERVALS else None,
    }


def _normalize_rr_interval(value: int | float | str) -> int | None:
    try:
        interval = float(value)
    except (TypeError, ValueError):
        return None

    if interval < SECONDS_TO_MS_THRESHOLD:
        interval *= 1000

    if interval < MIN_RR_INTERVAL_MS or interval > MAX_RR_INTERVAL_MS:
        return None

    return round(interval)


def _quality(sample_count: int) -> str:
    if sample_count == 0:
        return "missing_rr_intervals"
    if sample_count < MIN_USABLE_RR_INTERVALS:
        return "insufficient_rr_intervals"
    return "usable"


def _rmssd(intervals: list[int]) -> float | None:
    if len(intervals) < MIN_SUCCESSIVE_INTERVALS:
        return None

    squared_diffs = [(current - previous) ** 2 for previous, current in pairwise(intervals)]
    return round(math.sqrt(mean(squared_diffs)), 1)


def _dfa_alpha1(intervals: list[int]) -> float | None:  # noqa: C901
    centered = [value - mean(intervals) for value in intervals]
    walk = []
    cumulative = 0.0
    for value in centered:
        cumulative += value
        walk.append(cumulative)

    window_sizes = [4, 8, 16, 32]
    fluctuations: list[tuple[float, float]] = []
    for window_size in window_sizes:
        if len(walk) < window_size * 2:
            continue

        residuals: list[float] = []
        for start in range(0, len(walk) - window_size + 1, window_size):
            segment = walk[start : start + window_size]
            residuals.extend(_detrended_residuals(segment))

        if residuals:
            fluctuation = math.sqrt(mean(value * value for value in residuals))
            if fluctuation > 0:
                fluctuations.append((math.log(window_size), math.log(fluctuation)))

    if len(fluctuations) < MIN_SUCCESSIVE_INTERVALS:
        return None

    x_values = [point[0] for point in fluctuations]
    y_values = [point[1] for point in fluctuations]
    x_mean = mean(x_values)
    y_mean = mean(y_values)
    denominator = sum((x - x_mean) ** 2 for x in x_values)
    if denominator == 0:
        return None

    slope = sum((x - x_mean) * (y - y_mean) for x, y in fluctuations) / denominator
    return round(slope, 3)


def _detrended_residuals(segment: list[float]) -> list[float]:
    x_values = list(range(len(segment)))
    x_mean = mean(x_values)
    y_mean = mean(segment)
    denominator = sum((x - x_mean) ** 2 for x in x_values)
    slope = (
        sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, segment, strict=True))
        / denominator
        if denominator
        else 0.0
    )
    intercept = y_mean - slope * x_mean

    return [y - (slope * x + intercept) for x, y in zip(x_values, segment, strict=True)]
