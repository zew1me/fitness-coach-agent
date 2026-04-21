from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

# Source of truth for a single fitness metric value.
# "user" = athlete typed it; "file" = extracted from GPX/FIT/screenshot;
# "estimated" = derived by the system from workout patterns.
ThresholdSource = Literal["user", "file", "estimated"]


class ThresholdValue(BaseModel):
    """Structured metric value with provenance and temporal context."""

    value: float
    unit: str
    source: ThresholdSource = "user"
    measured_at: date | None = None
    notes: str | None = None


class BestTime(BaseModel):
    distance_label: str
    time_seconds: int
    measured_at: date | None = None


class AthleteProfile(BaseModel):
    user_id: str
    display_name: str | None = None
    biological_sex: str | None = None  # male | female | not_specified
    hormone_status: str | None = (
        None  # endogenous | hrt_estrogen | hrt_testosterone | not_specified
    )
    birth_date: date | None = None
    weight_kg: float | None = None
    height_cm: float | None = None
    resting_hr_bpm: int | None = None
    max_hr_bpm: int | None = None
    primary_sports: list[str] = Field(default_factory=list)
    weekly_available_hours: float | None = None
    coaching_state: str = "onboarding"
    specialization_pct: int = 80
    onboarding_collected: dict[str, bool] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    injuries_rehab: list[str] = Field(default_factory=list)
    notes: str | None = None
    dietary_restrictions: list[str] = Field(default_factory=list)
    nutrition_notes: str | None = None

    # Source metadata for max HR (the flat int stays for backward compat)
    max_hr_source: ThresholdSource | None = None
    max_hr_measured_at: date | None = None
    max_hr_notes: str | None = None

    # Source metadata for body weight (optional; framed as training-math only)
    weight_source: ThresholdSource | None = None
    weight_measured_at: date | None = None
    weight_notes: str | None = None

    # Personal bests across distances
    best_times: list[BestTime] = Field(default_factory=list)

    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def age(self) -> int | None:
        if self.birth_date is None:
            return None
        today = date.today()
        return (
            today.year
            - self.birth_date.year
            - ((today.month, today.day) < (self.birth_date.month, self.birth_date.day))
        )

    def max_hr_threshold_value(self) -> ThresholdValue | None:
        if self.max_hr_bpm is None:
            return None
        return ThresholdValue(
            value=self.max_hr_bpm,
            unit="bpm",
            source=self.max_hr_source or "user",
            measured_at=self.max_hr_measured_at,
            notes=self.max_hr_notes,
        )

    def weight_threshold_value(self) -> ThresholdValue | None:
        if self.weight_kg is None:
            return None
        return ThresholdValue(
            value=self.weight_kg,
            unit="kg",
            source=self.weight_source or "user",
            measured_at=self.weight_measured_at,
            notes=self.weight_notes,
        )


class SportThreshold(BaseModel):
    id: str | None = None
    user_id: str
    sport: str

    lt1_power_watts: int | None = None
    lt1_pace_sec_per_km: int | None = None
    lt1_hr_bpm: int | None = None

    lt2_power_watts: int | None = None
    lt2_pace_sec_per_km: int | None = None
    lt2_hr_bpm: int | None = None

    css_sec_per_100: int | None = None

    zones: list[dict] = Field(default_factory=list)

    estimation_method: str = "manual"
    estimation_source: str | None = None
    confidence: str = "low"
    source: ThresholdSource | None = None

    effective_from: date = Field(default_factory=date.today)
    superseded_at: datetime | None = None

    @property
    def derived_source(self) -> ThresholdSource:
        """Map estimation_method to the 3-way source enum if explicit source is absent."""
        if self.source:
            return self.source
        if self.estimation_method == "manual":
            return "user"
        if self.estimation_method in ("field_test", "race_time"):
            return "file"
        return "estimated"

    def as_threshold_value(self, value: int | float, unit: str) -> ThresholdValue:
        return ThresholdValue(
            value=value,
            unit=unit,
            source=self.derived_source,
            measured_at=self.effective_from,
            notes=self.estimation_source,
        )


class RecoveryLog(BaseModel):
    id: str | None = None
    user_id: str
    log_date: date

    sleep_duration_hours: float | None = None
    sleep_score: int | None = None
    sleep_consistency_pct: float | None = None
    hrv_ms: float | None = None
    resting_hr_bpm: int | None = None
    body_battery: int | None = None
    stress_score: int | None = None
    subjective_energy: int | None = None
    notes: str | None = None

    source: str = "manual"


class ScheduleAvailability(BaseModel):
    id: str | None = None
    user_id: str
    weekly_pattern: dict = Field(default_factory=dict)


class ScheduleOverride(BaseModel):
    id: str | None = None
    user_id: str
    override_date: date
    available: bool = False
    max_hours: float | None = None
    reason: str | None = None
