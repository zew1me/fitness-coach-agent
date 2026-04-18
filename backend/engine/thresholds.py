"""LT1/LT2 threshold estimation and age-based CTL ceiling guidance.

Running: Daniels' VDOT lookup from race times.
Cycling: Coggan FTP estimation from time-trial tests.
Age-based CTL ceilings: soft guidance for realistic goal-setting.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── Daniels' VDOT table (abridged) ──────────────────────────────
# Maps VDOT → (5K time in seconds, easy pace sec/km, threshold pace sec/km)
# Source: Jack Daniels' Running Formula, 3rd edition
_VDOT_TABLE: list[tuple[int, int, int, int]] = [
    # (vdot, 5k_secs, easy_pace_sec_km, threshold_pace_sec_km)
    (30, 1860, 447, 390),  # 31:00 5K
    (32, 1740, 429, 375),  # 29:00
    (34, 1620, 411, 360),  # 27:00
    (36, 1530, 396, 348),  # 25:30
    (38, 1440, 381, 336),  # 24:00
    (40, 1365, 369, 324),  # 22:45
    (42, 1290, 357, 315),  # 21:30
    (44, 1224, 345, 306),  # 20:24
    (46, 1164, 336, 297),  # 19:24
    (48, 1110, 327, 291),  # 18:30
    (50, 1056, 318, 282),  # 17:36
    (52, 1008, 309, 276),  # 16:48
    (54, 960, 300, 270),   # 16:00
    (56, 918, 294, 264),   # 15:18
    (58, 876, 285, 258),   # 14:36
    (60, 840, 279, 252),   # 14:00
    (62, 804, 273, 246),   # 13:24
    (64, 774, 267, 240),   # 12:54
    (66, 744, 261, 237),   # 12:24
    (68, 714, 255, 231),   # 11:54
    (70, 690, 249, 225),   # 11:30
    (75, 630, 237, 213),   # 10:30
    (80, 576, 225, 201),   # 9:36
    (85, 528, 213, 192),   # 8:48
]


@dataclass(frozen=True)
class RunningThresholds:
    vdot: int
    lt2_pace_sec_km: int
    lt1_pace_sec_km: int  # LT2 + ~37s (Seiler)
    easy_pace_sec_km: int


def estimate_running_thresholds(
    race_time_seconds: int,
    race_distance_meters: int = 5000,
) -> RunningThresholds:
    """Estimate LT1/LT2 running paces from a race result.

    Supports 5K, 10K, half marathon, and marathon distances.
    Uses Daniels' VDOT equivalence and Riegel's formula for distance conversion.
    """
    # Normalize to equivalent 5K time using Riegel's formula: T2 = T1 * (D2/D1)^1.06
    if race_distance_meters != 5000:
        ratio = (5000 / race_distance_meters) ** 1.06
        race_time_seconds = round(race_time_seconds * ratio)

    # Find closest VDOT from 5K time
    best_vdot = _VDOT_TABLE[0]
    for entry in _VDOT_TABLE:
        if entry[1] >= race_time_seconds:
            best_vdot = entry
        else:
            break

    vdot, _, easy_pace, threshold_pace = best_vdot
    lt1_pace = threshold_pace + 37  # Seiler: LT1 ≈ 30-45s/km slower than LT2

    return RunningThresholds(
        vdot=vdot,
        lt2_pace_sec_km=threshold_pace,
        lt1_pace_sec_km=lt1_pace,
        easy_pace_sec_km=easy_pace,
    )


@dataclass(frozen=True)
class CyclingThresholds:
    ftp_watts: int
    lt1_watts: int


def estimate_cycling_thresholds(
    test_power_watts: int,
    test_duration_minutes: int,
) -> CyclingThresholds:
    """Estimate FTP from a time-trial power test.

    Standard Coggan adjustments:
    - 60 min: direct FTP
    - 20 min: × 0.95
    - 8 min: × 0.90
    - 12 min: × 0.925 (interpolated)
    - 5 min: × 0.85
    """
    if test_duration_minutes >= 55:
        factor = 1.0
    elif test_duration_minutes >= 18:
        factor = 0.95
    elif test_duration_minutes >= 10:
        factor = 0.925
    elif test_duration_minutes >= 7:
        factor = 0.90
    else:
        factor = 0.85

    ftp = round(test_power_watts * factor)
    lt1 = round(ftp * 0.75)
    return CyclingThresholds(ftp_watts=ftp, lt1_watts=lt1)


@dataclass(frozen=True)
class CTLCeilingEstimate:
    """Soft guidance for realistic CTL targets by age bracket."""

    age_bracket: str
    elite_ctl: int
    committed_amateur_ctl: int
    recreational_ctl: int
    recovery_week_frequency: str
    notes: str


def estimate_ctl_ceiling(age: int | None, biological_sex: str = "not_specified") -> CTLCeilingEstimate:
    """Return age-appropriate CTL ceiling guidance. These are soft guides, not hard caps."""
    if age is None or age < 18:
        return CTLCeilingEstimate(
            age_bracket="unknown/youth",
            elite_ctl=80,
            committed_amateur_ctl=50,
            recreational_ctl=30,
            recovery_week_frequency="every 3 weeks",
            notes="Limited data. Prioritize development over load.",
        )

    if age < 30:
        return CTLCeilingEstimate(
            age_bracket="under 30",
            elite_ctl=150,
            committed_amateur_ctl=100,
            recreational_ctl=50,
            recovery_week_frequency="every 4 weeks",
            notes="Peak recovery capacity. Can tolerate higher ramp rates.",
        )

    if age < 40:
        return CTLCeilingEstimate(
            age_bracket="30-39",
            elite_ctl=130,
            committed_amateur_ctl=85,
            recreational_ctl=45,
            recovery_week_frequency="every 3-4 weeks",
            notes="Still strong capacity. Recovery starts to matter more.",
        )

    if age < 50:
        return CTLCeilingEstimate(
            age_bracket="40-49",
            elite_ctl=110,
            committed_amateur_ctl=70,
            recreational_ctl=40,
            recovery_week_frequency="every 3 weeks",
            notes="Recovery time increases. Conservative TSS ramp recommended.",
        )

    return CTLCeilingEstimate(
        age_bracket="50+",
        elite_ctl=90,
        committed_amateur_ctl=60,
        recreational_ctl=35,
        recovery_week_frequency="every 2-3 weeks",
        notes="Prioritize consistency and recovery. Taper sensitivity increases.",
    )
