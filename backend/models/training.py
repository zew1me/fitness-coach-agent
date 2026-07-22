from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class Activity(BaseModel):
    id: str | None = None
    user_id: str
    sport: str
    activity_date: date
    started_at: datetime | None = None

    duration_seconds: int | None = None
    distance_meters: float | None = None
    elevation_gain_meters: float | None = None
    avg_hr_bpm: int | None = None
    max_hr_bpm: int | None = None
    avg_power_watts: int | None = None
    normalized_power_watts: int | None = None
    avg_pace_sec_per_km: int | None = None
    avg_cadence_rpm: int | None = None

    tss: float | None = None
    intensity_factor: float | None = None
    zone_distribution: dict[str, float] | None = None

    rpe: int | None = None
    athlete_notes: str | None = None
    fatigue_notes: str | None = None
    fueling_notes: str | None = None

    source: str = "manual"
    source_file_key: str | None = None
    raw_extraction: dict[str, Any] | None = None
    summary_schema_version: int = 1
    activity_summary: dict[str, Any] = Field(default_factory=dict)

    planned_workout_id: str | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None


class DailyLoadSnapshot(BaseModel):
    id: str | None = None
    user_id: str
    snapshot_date: date
    sport: str | None = None  # None = aggregate

    daily_tss: float = 0
    ctl: float = 0
    atl: float = 0
    tsb: float = 0


GoalType = Literal["event", "mountain", "improvement", "maintenance", "secondary"]

# Compatibility aliases are accepted at the backend boundary, then persisted canonically.
GOAL_TYPE_ALIASES: dict[str, GoalType] = {"race": "event"}


class _GoalPayloadFields(BaseModel):
    goal_type: GoalType | None = None
    sport: str | None = None
    title: str | None = None
    description: str | None = None
    target_date: date | None = None

    target_ctl: float | None = None
    target_metric_name: str | None = None
    target_metric_value: float | None = None

    course_distance_meters: float | None = None
    course_elevation_gain_meters: float | None = None
    course_avg_grade_pct: float | None = None
    course_max_grade_pct: float | None = None
    course_profile: dict[str, Any] | None = None
    course_profile_notes: str | None = None

    improvement_metric: str | None = None
    improvement_target_value: float | None = None
    improvement_baseline_value: float | None = None

    priority: int | None = None
    status: str | None = None

    @field_validator("goal_type", mode="before")
    @classmethod
    def normalize_goal_type_alias(cls, value: object) -> object:
        if isinstance(value, str):
            return GOAL_TYPE_ALIASES.get(value, value)
        return value


class GoalCreatePayload(_GoalPayloadFields):
    """Strict fields accepted when creating a goal."""

    goal_type: GoalType
    title: str


class GoalUpdatePayload(_GoalPayloadFields):
    """Partial goal fields; explicit null values remain distinguishable from omissions."""


class Goal(BaseModel):
    id: str | None = None
    user_id: str

    goal_type: GoalType
    sport: str | None = None
    title: str
    description: str | None = None
    target_date: date | None = None

    target_ctl: float | None = None
    target_metric_name: str | None = None
    target_metric_value: float | None = None

    # Course / terrain spec
    course_distance_meters: float | None = None
    course_elevation_gain_meters: float | None = None
    course_avg_grade_pct: float | None = None
    course_max_grade_pct: float | None = None
    course_profile: dict[str, Any] | None = None

    # Improvement goal spec
    improvement_metric: str | None = None
    improvement_target_value: float | None = None
    improvement_baseline_value: float | None = None

    priority: int = 1
    status: str = "active"

    created_at: datetime | None = None
    updated_at: datetime | None = None


class TrainingPlan(BaseModel):
    id: str | None = None
    user_id: str

    title: str
    plan_type: str  # full_cycle | mesocycle | weekly | adjustment
    status: str = "active"

    start_date: date
    end_date: date
    target_goal_id: str | None = None

    phases: list[dict[str, Any]] = Field(default_factory=list)
    generation_context: dict[str, Any] | None = None
    weekly_tss_target: float | None = None
    weekly_hours_target: float | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None


class PlanWorkout(BaseModel):
    id: str | None = None
    plan_id: str
    user_id: str

    workout_date: date
    day_of_week: int
    week_number: int
    phase_name: str | None = None

    sport: str
    title: str
    description: str | None = None
    workout_type: str

    target_duration_minutes: int | None = None
    target_distance_meters: float | None = None
    target_tss: float | None = None
    target_intensity_factor: float | None = None
    zone_targets: dict[str, float] | None = None
    intervals: list[dict[str, Any]] | None = None

    status: str = "scheduled"
    actual_activity_id: str | None = None
    completion_source: Literal["auto_matched", "athlete_confirmed", "coach_confirmed"] | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None


class AdaptedPlan(BaseModel):
    """Legacy-compatible plan summary returned from plan generation."""

    user_id: str
    plan_id: str | None = None
    title: str
    summary: str
    start_date: date
    end_date: date
    phases: list[dict[str, Any]] = Field(default_factory=list)
    weekly_tss_target: float | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
