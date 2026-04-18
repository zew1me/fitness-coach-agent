"""Training Stress Score computation for multiple modalities.

- Cycling with power: standard TSS (Coggan)
- Running with pace: rTSS via Normalized Graded Pace
- Heart-rate only: hrTSS via Banister TRIMP
- RPE-only fallback: estimated TSS from subjective effort
"""

from __future__ import annotations

import math


def cycling_tss(
    duration_seconds: int,
    normalized_power: int,
    ftp: int,
) -> float:
    """Standard power-based TSS: (s * NP * IF) / (FTP * 3600) * 100."""
    if ftp <= 0 or duration_seconds <= 0:
        return 0.0
    intensity_factor = normalized_power / ftp
    return (duration_seconds * normalized_power * intensity_factor) / (ftp * 3600) * 100


def running_tss(
    duration_seconds: int,
    avg_pace_sec_km: int,
    threshold_pace_sec_km: int,
) -> float:
    """Running TSS: (hours) * (threshold_pace / actual_pace)^2 * 100.

    Faster actual pace (lower sec/km) → higher IF → higher rTSS.
    """
    if threshold_pace_sec_km <= 0 or avg_pace_sec_km <= 0 or duration_seconds <= 0:
        return 0.0
    hours = duration_seconds / 3600
    # IF for running: threshold / actual (faster = lower number = higher ratio)
    intensity_factor = threshold_pace_sec_km / avg_pace_sec_km
    return hours * (intensity_factor ** 2) * 100


def hr_tss(
    duration_seconds: int,
    avg_hr: int,
    resting_hr: int,
    max_hr: int,
    biological_sex: str = "not_specified",
) -> float:
    """Heart-rate based TSS using Banister TRIMP with sex-specific coefficients.

    TRIMP = duration_minutes * HRR_fraction * 0.64 * e^(sex_coeff * HRR_fraction)
    Scaled to approximate power-based TSS range (~60 for 1hr at threshold).
    """
    if max_hr <= resting_hr or duration_seconds <= 0:
        return 0.0
    duration_min = duration_seconds / 60
    hr_reserve_fraction = (avg_hr - resting_hr) / (max_hr - resting_hr)
    hr_reserve_fraction = max(0.0, min(1.0, hr_reserve_fraction))

    # Sex-specific exponential coefficient (Banister 1991)
    if biological_sex == "female" or biological_sex == "hrt_estrogen":
        sex_coeff = 1.67
    else:
        sex_coeff = 1.92

    trimp = duration_min * hr_reserve_fraction * 0.64 * math.exp(sex_coeff * hr_reserve_fraction)
    # Scale TRIMP to approximate TSS: 1 hour at threshold ≈ TRIMP ~100, TSS ~60
    return trimp * 0.6


def rpe_tss(duration_seconds: int, rpe: int) -> float:
    """Rough TSS estimate from subjective RPE (1-10) when no other data available.

    TSS ≈ hours * (RPE/10)^2 * 100
    """
    if duration_seconds <= 0 or rpe <= 0:
        return 0.0
    hours = duration_seconds / 3600
    return hours * (rpe / 10) ** 2 * 100


def compute_tss(
    duration_seconds: int,
    *,
    sport: str = "general",
    normalized_power: int | None = None,
    ftp: int | None = None,
    avg_pace_sec_km: int | None = None,
    threshold_pace_sec_km: int | None = None,
    avg_hr: int | None = None,
    resting_hr: int | None = None,
    max_hr: int | None = None,
    biological_sex: str = "not_specified",
    rpe: int | None = None,
) -> float:
    """Compute TSS using the best available data, cascading through modalities."""
    # Power-based (cycling or any sport with power)
    if normalized_power is not None and ftp is not None and ftp > 0:
        return cycling_tss(duration_seconds, normalized_power, ftp)

    # Pace-based (running)
    if (
        sport == "running"
        and avg_pace_sec_km is not None
        and threshold_pace_sec_km is not None
    ):
        return running_tss(duration_seconds, avg_pace_sec_km, threshold_pace_sec_km)

    # Heart rate based
    if avg_hr is not None and resting_hr is not None and max_hr is not None:
        return hr_tss(duration_seconds, avg_hr, resting_hr, max_hr, biological_sex)

    # RPE fallback
    if rpe is not None:
        return rpe_tss(duration_seconds, rpe)

    return 0.0


def compute_normalized_power(power_stream: list[int], sample_rate_seconds: int = 1) -> int:
    """Compute Normalized Power from a power data stream.

    NP = (mean of (30s rolling avg)^4)^0.25
    """
    if not power_stream:
        return 0
    window_samples = max(1, 30 // sample_rate_seconds)
    if len(power_stream) < window_samples:
        return round(sum(power_stream) / len(power_stream))

    # Rolling 30-second average
    rolling_sum = sum(power_stream[:window_samples])
    fourth_powers: list[float] = []

    for i in range(window_samples, len(power_stream)):
        avg = rolling_sum / window_samples
        fourth_powers.append(avg ** 4)
        rolling_sum += power_stream[i] - power_stream[i - window_samples]

    # Include the last window
    avg = rolling_sum / window_samples
    fourth_powers.append(avg ** 4)

    if not fourth_powers:
        return 0
    return round((sum(fourth_powers) / len(fourth_powers)) ** 0.25)
