from datetime import UTC, date, datetime

from pydantic import BaseModel, Field


class AthleteProfile(BaseModel):
    age: int | None = None
    constraints: list[str] = Field(default_factory=list)
    cycling_ftp_watts: int | None = None
    goals: list[str] = Field(default_factory=list)
    injuries_rehab: list[str] = Field(default_factory=list)
    notes: str | None = None
    user_id: str
    weight_kg: float | None = None


class CheckInInput(BaseModel):
    effective_date: date | None = None
    image_count: int = 0
    raw_text: str
    user_id: str


class PlanDay(BaseModel):
    day_index: int
    focus: str
    notes: str


class AdaptedPlan(BaseModel):
    days: list[PlanDay]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    hours: float
    summary: str
    trend: str
    user_id: str
