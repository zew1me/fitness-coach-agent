"""Periodization logic for training plan skeleton generation.

Seiler-influenced phase selection, weekly TSS targets, and workout type distribution.
Works backward from target event date to allocate Base → Build → Peak → Taper.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class PhaseTemplate:
    name: str
    min_weeks: int
    max_weeks: int
    z1_z2_pct: int  # percentage of TSS in Z1-Z2
    weekly_tss_ramp_pct: float  # per-week increase (e.g., 0.05 = 5%)
    max_hiit_per_week: int
    description: str


PHASE_TEMPLATES = {
    "base": PhaseTemplate(
        name="Base",
        min_weeks=4,
        max_weeks=8,
        z1_z2_pct=80,
        weekly_tss_ramp_pct=0.04,
        max_hiit_per_week=2,
        description="Aerobic volume building. Mostly Z1-Z2 with light tempo.",
    ),
    "build": PhaseTemplate(
        name="Build",
        min_weeks=4,
        max_weeks=6,
        z1_z2_pct=75,
        weekly_tss_ramp_pct=0.06,
        max_hiit_per_week=3,
        description="Threshold and VO2max development. Increasing intensity.",
    ),
    "peak": PhaseTemplate(
        name="Peak",
        min_weeks=2,
        max_weeks=3,
        z1_z2_pct=70,
        weekly_tss_ramp_pct=0.0,
        max_hiit_per_week=3,
        description="Race-specific sharpening. Intensity high, volume steady or decreasing.",
    ),
    "taper": PhaseTemplate(
        name="Taper",
        min_weeks=1,
        max_weeks=2,
        z1_z2_pct=80,
        weekly_tss_ramp_pct=-0.35,
        max_hiit_per_week=2,
        description="Volume drops 30-50%. Maintain intensity. Freshness before event.",
    ),
    "recovery": PhaseTemplate(
        name="Recovery",
        min_weeks=1,
        max_weeks=1,
        z1_z2_pct=90,
        weekly_tss_ramp_pct=-0.40,
        max_hiit_per_week=0,
        description="Active recovery. Reduced volume and intensity.",
    ),
}

SHORT_EVENT_MAX_WEEKS = 8


@dataclass
class PhasePlan:
    name: str
    start_week: int
    end_week: int
    focus: str
    target_weekly_tss: float
    z1_z2_pct: int
    max_hiit_per_week: int
    description: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "start_week": self.start_week,
            "end_week": self.end_week,
            "focus": self.focus,
            "target_weekly_tss": round(self.target_weekly_tss),
            "z1_z2_pct": self.z1_z2_pct,
            "max_hiit_per_week": self.max_hiit_per_week,
            "description": self.description,
        }


@dataclass
class PlanSkeleton:
    phases: list[PhasePlan]
    total_weeks: int
    start_date: date
    end_date: date
    starting_weekly_tss: float


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def build_plan_skeleton(  # noqa: PLR0913
    *,
    current_ctl: float,
    target_date: date | None,
    available_hours_per_week: float,
    goal_type: str = "event",
    recovery_week_frequency: int = 4,
    start_date: date | None = None,
) -> PlanSkeleton:
    """Generate a periodized plan skeleton.

    For event goals, works backward from target_date.
    For maintenance/improvement, creates rolling mesocycles.
    """
    plan_start = start_date or date.today()

    # Estimate starting weekly TSS from CTL (CTL ≈ average daily TSS, so weekly ≈ CTL * 7)
    starting_weekly_tss = _clamp(current_ctl * 7, 100, available_hours_per_week * 80)

    if target_date and goal_type in ("event", "mountain"):
        return _event_periodization(
            plan_start=plan_start,
            target_date=target_date,
            starting_weekly_tss=starting_weekly_tss,
            recovery_week_frequency=recovery_week_frequency,
        )
    return _rolling_periodization(
        plan_start=plan_start,
        starting_weekly_tss=starting_weekly_tss,
        total_weeks=12,
        recovery_week_frequency=recovery_week_frequency,
        goal_type=goal_type,
    )


def _event_periodization(
    *,
    plan_start: date,
    target_date: date,
    starting_weekly_tss: float,
    recovery_week_frequency: int,
) -> PlanSkeleton:
    """Work backward from event date: Taper → Peak → Build → fill remaining with Base."""
    total_days = (target_date - plan_start).days
    total_weeks = max(4, total_days // 7)

    # Allocate phases backward
    taper_weeks = 1 if total_weeks <= SHORT_EVENT_MAX_WEEKS else 2
    peak_weeks = min(3, max(2, total_weeks // 6))
    build_weeks = min(6, max(4, total_weeks // 3))
    base_weeks = max(0, total_weeks - taper_weeks - peak_weeks - build_weeks)

    phases: list[PhasePlan] = []
    week = 1
    tss = starting_weekly_tss

    # Insert recovery weeks into base and build
    def _add_phase(name: str, weeks: int, tss_start: float) -> float:
        if weeks <= 0:
            return tss_start
        tmpl = PHASE_TEMPLATES[name.lower()]
        current_tss = tss_start
        block_start = week

        for w in range(weeks):
            actual_week = block_start + w
            # Insert recovery week every N weeks (not in taper/peak)
            if (
                name.lower() in ("base", "build")
                and w > 0
                and (actual_week - 1) % recovery_week_frequency == 0
            ):
                phases.append(
                    PhasePlan(
                        name="Recovery",
                        start_week=actual_week,
                        end_week=actual_week,
                        focus="recovery",
                        target_weekly_tss=current_tss * 0.6,
                        z1_z2_pct=90,
                        max_hiit_per_week=0,
                        description=PHASE_TEMPLATES["recovery"].description,
                    )
                )
            else:
                phases.append(
                    PhasePlan(
                        name=tmpl.name,
                        start_week=actual_week,
                        end_week=actual_week,
                        focus=name.lower(),
                        target_weekly_tss=current_tss,
                        z1_z2_pct=tmpl.z1_z2_pct,
                        max_hiit_per_week=tmpl.max_hiit_per_week,
                        description=tmpl.description,
                    )
                )
                # Ramp TSS (max 10% per week)
                ramp = min(0.10, tmpl.weekly_tss_ramp_pct)
                current_tss = current_tss * (1 + ramp)
        return current_tss

    tss = _add_phase("base", base_weeks, tss)
    week += base_weeks
    tss = _add_phase("build", build_weeks, tss)
    week += build_weeks
    _add_phase("peak", peak_weeks, tss)
    week += peak_weeks
    _add_phase("taper", taper_weeks, tss * 0.65)

    end_date = plan_start + timedelta(weeks=total_weeks)

    return PlanSkeleton(
        phases=phases,
        total_weeks=total_weeks,
        start_date=plan_start,
        end_date=end_date,
        starting_weekly_tss=starting_weekly_tss,
    )


def _rolling_periodization(
    *,
    plan_start: date,
    starting_weekly_tss: float,
    total_weeks: int,
    recovery_week_frequency: int,
    goal_type: str,
) -> PlanSkeleton:
    """Rolling mesocycles for maintenance/improvement (no target event date)."""
    phases: list[PhasePlan] = []
    tss = starting_weekly_tss

    focus = "base" if goal_type == "maintenance" else "build"
    tmpl = PHASE_TEMPLATES[focus]

    for w in range(1, total_weeks + 1):
        if w > 1 and (w - 1) % recovery_week_frequency == 0:
            phases.append(
                PhasePlan(
                    name="Recovery",
                    start_week=w,
                    end_week=w,
                    focus="recovery",
                    target_weekly_tss=tss * 0.6,
                    z1_z2_pct=90,
                    max_hiit_per_week=0,
                    description=PHASE_TEMPLATES["recovery"].description,
                )
            )
        else:
            phases.append(
                PhasePlan(
                    name=tmpl.name,
                    start_week=w,
                    end_week=w,
                    focus=focus,
                    target_weekly_tss=tss,
                    z1_z2_pct=tmpl.z1_z2_pct,
                    max_hiit_per_week=tmpl.max_hiit_per_week,
                    description=tmpl.description,
                )
            )
            ramp = min(0.10, tmpl.weekly_tss_ramp_pct)
            tss = tss * (1 + ramp)

    end_date = plan_start + timedelta(weeks=total_weeks)

    return PlanSkeleton(
        phases=phases,
        total_weeks=total_weeks,
        start_date=plan_start,
        end_date=end_date,
        starting_weekly_tss=starting_weekly_tss,
    )
