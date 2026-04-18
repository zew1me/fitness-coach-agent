from datetime import date, datetime

from pydantic import BaseModel, Field


class AthleteProfile(BaseModel):
    user_id: str
    display_name: str | None = None
    biological_sex: str | None = None  # male | female | not_specified
    hormone_status: str | None = None  # endogenous | hrt_estrogen | hrt_testosterone | not_specified
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

    zones: list[dict] = Field(default_factory=list)

    estimation_method: str = "manual"
    estimation_source: str | None = None
    confidence: str = "low"

    effective_from: date = Field(default_factory=date.today)
    superseded_at: datetime | None = None


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
