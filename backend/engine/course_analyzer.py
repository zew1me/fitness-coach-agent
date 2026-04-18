"""Course-specific training analysis for hill climbs, trail events, and mountain goals.

Estimates race targets from course profile + athlete thresholds,
and provides training emphasis recommendations for periodization.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CourseAnalysis:
    estimated_duration_seconds: int | None
    primary_training_emphasis: str  # e.g., "climbing_power", "uphill_running_economy"
    workout_type_weights: dict[str, float]  # extra weighting for periodization
    vam_target: float | None  # vertical ascent meters/hour (cycling)
    notes: str


def analyze_cycling_climb(
    *,
    distance_meters: float,
    elevation_gain_meters: float,
    avg_grade_pct: float,
    ftp_watts: int,
    weight_kg: float,
) -> CourseAnalysis:
    """Estimate performance and training needs for a cycling hill climb.

    Uses power-to-weight ratio and VAM (vertical ascent meters/hour) model.
    """
    # Power needed to maintain FTP on a climb: approximation
    # Gravity component: weight_kg * 9.81 * grade * speed
    # At FTP, estimate speed from P = (mass * g * grade + rolling + aero) * v
    # Simplified: on steep climb, VAM ≈ (FTP - 50) * 3600 / (weight_kg * 9.81)
    net_power = max(50, ftp_watts - 50)  # subtract ~50W for rolling/aero on climb
    vam = (net_power * 3600) / (weight_kg * 9.81)

    estimated_duration = None
    if vam > 0 and elevation_gain_meters > 0:
        climb_hours = elevation_gain_meters / vam
        # Add time for flat/downhill sections (rough: distance beyond climb)
        climb_distance = elevation_gain_meters / (avg_grade_pct / 100) if avg_grade_pct > 0 else 0
        extra_distance = max(0, distance_meters - climb_distance)
        extra_time_hours = extra_distance / 30000  # ~30km/h on flats
        estimated_duration = round((climb_hours + extra_time_hours) * 3600)

    return CourseAnalysis(
        estimated_duration_seconds=estimated_duration,
        primary_training_emphasis="climbing_power",
        workout_type_weights={
            "sweet_spot": 0.3,
            "threshold": 0.3,
            "hill_repeats": 0.2,
            "endurance": 0.15,
            "vo2max": 0.05,
        },
        vam_target=round(vam, 0),
        notes=(
            f"Target VAM: {round(vam)}m/hr at FTP. "
            f"Focus on sustained power near threshold with climbing-specific cadence (70-80rpm). "
            f"Include long climbing intervals at sweet spot and threshold."
        ),
    )


def analyze_running_climb(
    *,
    distance_meters: float,
    elevation_gain_meters: float,
    avg_grade_pct: float,
    lt2_pace_sec_km: int,
) -> CourseAnalysis:
    """Estimate performance and training needs for a trail/hill running event.

    Uses grade-adjusted pace (GAP) model.
    """
    # GAP adjustment: roughly +12s per 1% uphill grade, -6s per 1% downhill
    # For net uphill event, compute adjusted pace
    gap_adjustment = round(avg_grade_pct * 12)  # seconds per km slower
    adjusted_pace = lt2_pace_sec_km + gap_adjustment

    distance_km = distance_meters / 1000
    # Can't sustain LT2 for long climbs; adjust for duration
    # Rough: if >30min at threshold, add fatigue factor
    base_duration = adjusted_pace * distance_km
    fatigue_factor = 1.0 + max(0, (base_duration - 1800) / 7200) * 0.05
    estimated_duration = round(base_duration * fatigue_factor)

    return CourseAnalysis(
        estimated_duration_seconds=estimated_duration,
        primary_training_emphasis="uphill_running_economy",
        workout_type_weights={
            "hill_repeats": 0.3,
            "tempo": 0.25,
            "long_run": 0.2,
            "threshold": 0.15,
            "strength": 0.1,
        },
        vam_target=None,
        notes=(
            f"Grade-adjusted pace: ~{adjusted_pace}s/km. "
            f"Focus on hill repeats, uphill tempo runs, and strength work for climbing economy. "
            f"Include long runs with elevation to build durability."
        ),
    )


def analyze_mountain_objective(
    *,
    elevation_gain_meters: float,
    estimated_hours: float | None = None,
) -> CourseAnalysis:
    """Training analysis for mountain objectives (e.g., summit Mt. Rainier).

    Focus is on aerobic capacity, durability under load (pack weight), and elevation tolerance.
    """
    # Rough CTL target for mountain objectives
    # Rainier-class (3000m+ gain): CTL ~60-80
    # Moderate peak (1500m gain): CTL ~40-50
    if elevation_gain_meters > 2500:
        emphasis = "high_altitude_endurance"
        notes = (
            "High-altitude mountaineering objective. "
            "Build aerobic base with long, loaded hikes. "
            "Include back-to-back long days to simulate summit push. "
            "Strength training for load carrying is essential."
        )
    else:
        emphasis = "mountain_endurance"
        notes = (
            "Mountain objective with significant vertical. "
            "Focus on uphill hiking/running endurance and loaded carries. "
            "Include long days with sustained climbing."
        )

    return CourseAnalysis(
        estimated_duration_seconds=round(estimated_hours * 3600) if estimated_hours else None,
        primary_training_emphasis=emphasis,
        workout_type_weights={
            "long_run": 0.3,
            "endurance": 0.25,
            "hill_repeats": 0.2,
            "strength": 0.15,
            "tempo": 0.1,
        },
        vam_target=None,
        notes=notes,
    )
