"""Training zone calculation per sport.

Cycling: Coggan/Seiler hybrid power zones from FTP (LT2 power).
Running: Daniels' VDOT-derived pace zones from LT2 pace.
Heart-rate: Seiler 3-zone expanded to 5-zone model.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Zone:
    number: int
    name: str
    # Power (watts) — cycling
    power_low: int | None = None
    power_high: int | None = None
    # Pace (seconds per km) — running (lower number = faster)
    pace_low_sec_km: int | None = None
    pace_high_sec_km: int | None = None
    # Heart rate
    hr_low: int | None = None
    hr_high: int | None = None

    def to_dict(self) -> dict:
        d: dict = {"zone": self.number, "name": self.name}
        if self.power_low is not None:
            d["power_low"] = self.power_low
            d["power_high"] = self.power_high
        if self.pace_low_sec_km is not None:
            d["pace_low_sec_km"] = self.pace_low_sec_km
            d["pace_high_sec_km"] = self.pace_high_sec_km
        if self.hr_low is not None:
            d["hr_low"] = self.hr_low
            d["hr_high"] = self.hr_high
        return d


def cycling_power_zones(ftp: int, lt1_watts: int | None = None) -> list[Zone]:
    """Compute cycling power zones from FTP. LT1 defaults to 75% FTP."""
    lt1 = lt1_watts if lt1_watts is not None else round(ftp * 0.75)
    return [
        Zone(1, "Recovery", power_low=0, power_high=round(ftp * 0.55)),
        Zone(2, "Endurance", power_low=round(ftp * 0.55) + 1, power_high=lt1),
        Zone(3, "Tempo", power_low=lt1 + 1, power_high=round(ftp * 0.90)),
        Zone(4, "Threshold", power_low=round(ftp * 0.91), power_high=round(ftp * 1.05)),
        Zone(5, "VO2max", power_low=round(ftp * 1.06), power_high=round(ftp * 1.20)),
        Zone(6, "Anaerobic", power_low=round(ftp * 1.21), power_high=round(ftp * 1.50)),
    ]


def running_pace_zones(
    lt2_pace_sec_km: int,
    lt1_pace_sec_km: int | None = None,
) -> list[Zone]:
    """Compute running pace zones from LT2 pace. LT1 defaults to LT2 + 37s/km."""
    lt1 = lt1_pace_sec_km if lt1_pace_sec_km is not None else lt2_pace_sec_km + 37
    return [
        # Note: for running, lower seconds = faster. Zone 1 = slowest.
        Zone(1, "Easy", pace_low_sec_km=lt1 + 30, pace_high_sec_km=lt1 + 60),
        Zone(2, "Aerobic", pace_low_sec_km=lt1 - 15, pace_high_sec_km=lt1 + 29),
        Zone(3, "Tempo", pace_low_sec_km=lt2_pace_sec_km + 10, pace_high_sec_km=lt1 - 16),
        Zone(4, "Threshold", pace_low_sec_km=lt2_pace_sec_km - 10, pace_high_sec_km=lt2_pace_sec_km + 9),
        Zone(5, "VO2max", pace_low_sec_km=lt2_pace_sec_km - 30, pace_high_sec_km=lt2_pace_sec_km - 11),
    ]


def hr_zones(
    max_hr: int,
    lt2_hr: int | None = None,
    lt1_hr: int | None = None,
) -> list[Zone]:
    """Compute HR zones from max HR. LT2 HR defaults to 87% max; LT1 to 75% max."""
    lt2 = lt2_hr if lt2_hr is not None else round(max_hr * 0.87)
    lt1 = lt1_hr if lt1_hr is not None else round(max_hr * 0.75)
    return [
        Zone(1, "Recovery", hr_low=0, hr_high=round(max_hr * 0.60)),
        Zone(2, "Endurance", hr_low=round(max_hr * 0.60) + 1, hr_high=lt1),
        Zone(3, "Tempo", hr_low=lt1 + 1, hr_high=lt2 - 1),
        Zone(4, "Threshold", hr_low=lt2, hr_high=round(max_hr * 0.95)),
        Zone(5, "VO2max", hr_low=round(max_hr * 0.95) + 1, hr_high=max_hr),
    ]


def compute_zones(
    sport: str,
    *,
    ftp_watts: int | None = None,
    lt1_power_watts: int | None = None,
    lt2_pace_sec_km: int | None = None,
    lt1_pace_sec_km: int | None = None,
    max_hr: int | None = None,
    lt2_hr: int | None = None,
    lt1_hr: int | None = None,
) -> list[Zone]:
    """Dispatch to the right zone calculator for a sport."""
    if sport == "cycling" and ftp_watts is not None:
        return cycling_power_zones(ftp_watts, lt1_power_watts)
    if sport == "running" and lt2_pace_sec_km is not None:
        return running_pace_zones(lt2_pace_sec_km, lt1_pace_sec_km)
    if max_hr is not None:
        return hr_zones(max_hr, lt2_hr, lt1_hr)
    return []
