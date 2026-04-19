"""CTL / ATL / TSB rolling computation.

CTL (Chronic Training Load) = 42-day exponentially weighted moving average of daily TSS
ATL (Acute Training Load)   = 7-day exponentially weighted moving average of daily TSS
TSB (Training Stress Balance) = CTL - ATL
"""

from __future__ import annotations

from datetime import date, timedelta

CTL_DAYS = 42
ATL_DAYS = 7


def compute_next_load(
    prev_ctl: float,
    prev_atl: float,
    daily_tss: float,
) -> tuple[float, float, float]:
    """Compute next day's CTL, ATL, TSB from previous values and today's TSS."""
    ctl = prev_ctl + (daily_tss - prev_ctl) / CTL_DAYS
    atl = prev_atl + (daily_tss - prev_atl) / ATL_DAYS
    tsb = ctl - atl
    return ctl, atl, tsb


def recompute_load_series(
    daily_tss_map: dict[date, float],
    start_date: date,
    end_date: date,
    initial_ctl: float = 0.0,
    initial_atl: float = 0.0,
) -> list[dict]:
    """Recompute CTL/ATL/TSB for a date range from a map of date → daily TSS.

    Returns a list of dicts suitable for upserting into daily_load_snapshots:
    [{"snapshot_date": date, "daily_tss": float, "ctl": float, "atl": float, "tsb": float}, ...]
    """
    results: list[dict] = []
    ctl = initial_ctl
    atl = initial_atl
    current = start_date

    while current <= end_date:
        tss = daily_tss_map.get(current, 0.0)
        ctl, atl, tsb = compute_next_load(ctl, atl, tss)
        results.append(
            {
                "snapshot_date": current,
                "daily_tss": round(tss, 1),
                "ctl": round(ctl, 1),
                "atl": round(atl, 1),
                "tsb": round(tsb, 1),
            }
        )
        current += timedelta(days=1)

    return results
