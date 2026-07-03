import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, TypeVar
from uuid import UUID, uuid4

from postgrest.exceptions import APIError as PostgRESTAPIError

from backend.config import settings
from backend.models.athlete import (
    AthleteProfile,
    RecoveryLog,
    ScheduleAvailability,
    ScheduleOverride,
    SportThreshold,
)
from backend.models.chat import (
    ChatMessage,
    ChatModelState,
    ChatModelStateReplaceRequest,
    ChatThread,
    MessageAttachment,
    MessagePart,
)
from backend.models.training import Activity, DailyLoadSnapshot, Goal, PlanWorkout, TrainingPlan
from supabase import Client, create_client


class RepositoryNotConfiguredError(RuntimeError):
    """Raised when database-backed operations are requested without Supabase config."""


class RecordNotFoundError(LookupError):
    """Raised when a requested record is absent in persistence."""


_DROP_FIELD = object()
SUMMARY_SCHEMA_VERSION = 1
SUMMARY_SCHEMA_NAME = "activity_summary_v1"

# After a deploy/migration, PostgREST briefly serves a stale schema cache and rejects
# requests with PGRST205 ("Could not find the table ... in the schema cache") even
# though the table exists. The window is usually sub-second, so a short retry rides it
# out instead of surfacing a 503 that degrades the chat turn (Sentry PYTHON-FASTAPI-6).
_SCHEMA_CACHE_ERROR_CODE = "PGRST205"
_SCHEMA_CACHE_RETRY_ATTEMPTS = 3
_SCHEMA_CACHE_RETRY_BACKOFF_SECONDS = 0.5

_T = TypeVar("_T")


async def _with_schema_cache_retry(operation: Callable[[], Awaitable[_T]]) -> _T:
    """Run ``operation`` retrying only transient PostgREST schema-cache misses.

    PostgREST rejects with PGRST205 *before* touching any rows, so re-running a
    state-changing operation (e.g. the lease acquire) on this error is safe. Any
    other error propagates immediately so real failures are not masked and the
    caller's request timeout is not burned on doomed retries.
    """
    backoff = _SCHEMA_CACHE_RETRY_BACKOFF_SECONDS
    for attempt in range(_SCHEMA_CACHE_RETRY_ATTEMPTS):
        try:
            return await operation()
        except PostgRESTAPIError as exc:
            if getattr(exc, "code", None) != _SCHEMA_CACHE_ERROR_CODE:
                raise
            if attempt == _SCHEMA_CACHE_RETRY_ATTEMPTS - 1:
                raise
            await asyncio.sleep(backoff * (attempt + 1))
    raise AssertionError("unreachable: schema-cache retry loop exited without returning")


_PROFILE_FIELDS = {
    "display_name",
    "biological_sex",
    "hormone_status",
    "birth_date",
    "weight_kg",
    "height_cm",
    "resting_hr_bpm",
    "max_hr_bpm",
    "primary_sports",
    "weekly_available_hours",
    "coaching_state",
    "specialization_pct",
    "onboarding_collected",
    "dietary_restrictions",
    "nutrition_notes",
    "notes",
    "injuries_rehab",
    "constraints",
    # Threshold source metadata (issue #54)
    "max_hr_source",
    "max_hr_measured_at",
    "max_hr_notes",
    "weight_source",
    "weight_measured_at",
    "weight_notes",
    "best_times",
}

_PROFILE_ENUM_VALUES = {
    "biological_sex": {"male", "female", "not_specified"},
    "hormone_status": {"endogenous", "hrt_estrogen", "hrt_testosterone", "not_specified"},
    "coaching_state": {"onboarding", "calibrating", "active", "paused"},
    "max_hr_source": {"user", "file", "estimated"},
    "weight_source": {"user", "file", "estimated"},
}

_PROFILE_ENUM_ALIASES = {
    "biological_sex": {
        "man": "male",
        "woman": "female",
    },
    "hormone_status": {},
    "coaching_state": {},
    "max_hr_source": {},
    "weight_source": {},
}

_NOT_SPECIFIED_ALIASES = {
    "",
    "n/a",
    "na",
    "none",
    "not_disclosed",
    "not_provided",
    "not_specified",
    "prefer_not_to_say",
    "unknown",
    "unspecified",
}


def _canonical_profile_enum_token(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _normalize_profile_enum_value(field: str, value: object) -> object:
    if value is None:
        return None
    if not isinstance(value, str):
        return _DROP_FIELD

    token = _canonical_profile_enum_token(value)
    if token in _NOT_SPECIFIED_ALIASES and "not_specified" in _PROFILE_ENUM_VALUES[field]:
        return "not_specified"

    alias = _PROFILE_ENUM_ALIASES[field].get(token)
    if alias is not None:
        return alias
    if token in _PROFILE_ENUM_VALUES[field]:
        return token
    return _DROP_FIELD


def _safe_athlete_profile_fields(fields: dict) -> dict[str, object]:
    safe_fields = {k: v for k, v in fields.items() if k in _PROFILE_FIELDS and v is not None}
    for array_field in ("primary_sports", "constraints", "injuries_rehab", "dietary_restrictions"):
        if array_field in safe_fields and isinstance(safe_fields[array_field], str):
            safe_fields[array_field] = [safe_fields[array_field]]

    for field in _PROFILE_ENUM_VALUES:
        if field not in safe_fields:
            continue
        normalized = _normalize_profile_enum_value(field, safe_fields[field])
        if normalized is _DROP_FIELD:
            del safe_fields[field]
        else:
            safe_fields[field] = normalized

    return safe_fields


def _base_activity_summary(activity: Activity) -> dict[str, Any]:
    source = activity.source
    raw_extraction = activity.raw_extraction or {}
    has_power = activity.avg_power_watts is not None or activity.normalized_power_watts is not None
    has_hr = activity.avg_hr_bpm is not None or activity.max_hr_bpm is not None
    has_gps = activity.distance_meters is not None or activity.elevation_gain_meters is not None
    return {
        "schema": SUMMARY_SCHEMA_NAME,
        "session": {
            "sport": activity.sport,
            "date_start": activity.activity_date.isoformat(),
        },
        "thresholds_used": {},
        "heart_rate": {},
        "power": {},
        "pace": {},
        "cadence": {},
        "load": {},
        "durability": {},
        "terrain": {},
        "environment": {},
        "fueling": {},
        "readiness": {},
        "subjective": {},
        "food_items": [],
        "additional_important_data": [],
        "estimates": {},
        "data_quality": {
            "source": source,
            "has_power": has_power,
            "has_hr": has_hr,
            "has_gps": has_gps,
            "has_rr_intervals": bool(raw_extraction.get("rr_interval_count")),
            "estimated_from_text": source == "text_extract",
        },
    }


def _add_activity_session_summary(summary: dict[str, Any], activity: Activity) -> None:
    if activity.started_at is not None:
        summary["session"]["started_at"] = activity.started_at.isoformat()
    if activity.duration_seconds is not None:
        summary["session"]["duration_moving_s"] = activity.duration_seconds
    if activity.distance_meters is not None:
        summary["session"]["distance_m"] = activity.distance_meters
    if activity.elevation_gain_meters is not None:
        summary["terrain"]["elevation_gain_m"] = activity.elevation_gain_meters


def _add_activity_stream_summary(summary: dict[str, Any], activity: Activity) -> None:
    if activity.avg_hr_bpm is not None:
        summary["heart_rate"]["avg_bpm"] = activity.avg_hr_bpm
    if activity.max_hr_bpm is not None:
        summary["heart_rate"]["max_bpm"] = activity.max_hr_bpm
    if activity.avg_power_watts is not None:
        summary["power"]["avg_w"] = activity.avg_power_watts
    if activity.normalized_power_watts is not None:
        summary["power"]["normalized_w"] = activity.normalized_power_watts
    if activity.avg_pace_sec_per_km is not None:
        summary["pace"]["avg_sec_per_km"] = activity.avg_pace_sec_per_km
    if activity.avg_cadence_rpm is not None:
        summary["cadence"]["avg_rpm"] = activity.avg_cadence_rpm


def _add_activity_context_summary(summary: dict[str, Any], activity: Activity) -> None:
    if activity.tss is not None:
        summary["load"]["primary_load"] = activity.tss
    if activity.intensity_factor is not None:
        summary["power"]["intensity_factor"] = activity.intensity_factor
    if activity.rpe is not None:
        summary["subjective"]["rpe_1_10"] = activity.rpe
    if activity.athlete_notes is not None:
        summary["subjective"]["athlete_notes"] = activity.athlete_notes
    if activity.fueling_notes is not None:
        summary["fueling"]["notes"] = activity.fueling_notes


def build_activity_summary_from_fields(activity: Activity) -> dict[str, Any]:
    summary = _base_activity_summary(activity)
    _add_activity_session_summary(summary, activity)
    _add_activity_stream_summary(summary, activity)
    _add_activity_context_summary(summary, activity)
    return summary


def _activity_payload(activity: Activity) -> dict[str, Any]:
    activity_to_persist = activity
    if not activity.activity_summary:
        activity_to_persist = activity.model_copy(
            update={
                "summary_schema_version": SUMMARY_SCHEMA_VERSION,
                "activity_summary": build_activity_summary_from_fields(activity),
            }
        )
    return activity_to_persist.model_dump(mode="json", exclude={"created_at", "updated_at"})


class SupabaseRepository:
    """Supabase-backed adapter for all domain persistence."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client or self._build_client()

    # ── Athlete Profiles ──────────────────────────────────────

    async def get_athlete_profile(self, user_id: str) -> AthleteProfile:
        client = self._require_client()
        response = client.table("athlete_profiles").select("*").eq("user_id", user_id).execute()
        rows = response.data or []
        if not rows:
            raise RecordNotFoundError(f"No athlete profile found for user '{user_id}'.")
        return AthleteProfile.model_validate(rows[0])

    async def upsert_athlete_profile(self, profile: AthleteProfile) -> AthleteProfile:
        client = self._require_client()
        payload = profile.model_dump(mode="json", exclude={"created_at", "updated_at"})
        response = client.table("athlete_profiles").upsert(payload, on_conflict="user_id").execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the upserted athlete profile row.")
        return AthleteProfile.model_validate(rows[0])

    async def update_athlete_profile_fields(self, user_id: str, fields: dict) -> AthleteProfile:
        """Merge a partial dict of fields into the existing profile row (upsert on user_id)."""
        client = self._require_client()
        safe_fields = _safe_athlete_profile_fields(fields)
        safe_fields["user_id"] = user_id
        response = (
            client.table("athlete_profiles").upsert(safe_fields, on_conflict="user_id").execute()
        )
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the updated athlete profile row.")
        return AthleteProfile.model_validate(rows[0])

    # ── Sport Thresholds ──────────────────────────────────────

    async def get_active_thresholds(self, user_id: str) -> list[SportThreshold]:
        client = self._require_client()
        response = (
            client.table("sport_thresholds")
            .select("*")
            .eq("user_id", user_id)
            .is_("superseded_at", "null")
            .order("effective_from", desc=True)
            .execute()
        )
        return [SportThreshold.model_validate(r) for r in (response.data or [])]

    async def upsert_sport_threshold(self, threshold: SportThreshold) -> SportThreshold:
        client = self._require_client()
        # Supersede existing active threshold for this sport
        client.table("sport_thresholds").update(
            {"superseded_at": datetime.now(UTC).isoformat()}
        ).eq("user_id", threshold.user_id).eq("sport", threshold.sport).is_(
            "superseded_at", "null"
        ).execute()

        payload = threshold.model_dump(
            mode="json", exclude={"created_at", "updated_at", "superseded_at"}
        )
        if not payload.get("id"):
            payload["id"] = str(uuid4())
        response = client.table("sport_thresholds").insert(payload).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the inserted sport threshold row.")
        return SportThreshold.model_validate(rows[0])

    # ── Activities ────────────────────────────────────────────

    async def create_activity(self, activity: Activity) -> Activity:
        client = self._require_client()
        payload = _activity_payload(activity)
        if not payload.get("id"):
            payload["id"] = str(uuid4())
        response = client.table("activities").insert(payload).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the inserted activity row.")
        return Activity.model_validate(rows[0])

    async def get_activity(self, user_id: str, activity_id: str) -> Activity:
        client = self._require_client()
        response = (
            client.table("activities")
            .select("*")
            .eq("user_id", user_id)
            .eq("id", activity_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            raise RecordNotFoundError(
                f"No activity found for user '{user_id}' and id '{activity_id}'."
            )
        return Activity.model_validate(rows[0])

    async def update_activity(self, activity: Activity) -> Activity:
        if activity.id is None:
            raise ValueError("Activity id is required for update.")
        client = self._require_client()
        payload = _activity_payload(activity)
        response = (
            client.table("activities")
            .update(payload)
            .eq("user_id", activity.user_id)
            .eq("id", activity.id)
            .execute()
        )
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the updated activity row.")
        return Activity.model_validate(rows[0])

    async def list_activities(
        self,
        user_id: str,
        *,
        sport: str | None = None,
        since: date | None = None,
        limit: int = 50,
    ) -> list[Activity]:
        client = self._require_client()
        query = client.table("activities").select("*").eq("user_id", user_id)
        if sport:
            query = query.eq("sport", sport)
        if since:
            query = query.gte("activity_date", since.isoformat())
        response = query.order("activity_date", desc=True).limit(limit).execute()
        return [Activity.model_validate(r) for r in (response.data or [])]

    # ── Daily Load Snapshots ──────────────────────────────────

    async def upsert_load_snapshots(
        self, user_id: str, snapshots: list[dict], sport: str | None = None
    ) -> None:
        client = self._require_client()
        for s in snapshots:
            s["user_id"] = user_id
            s["sport"] = sport
            if "id" not in s:
                s["id"] = str(uuid4())
            s["snapshot_date"] = (
                s["snapshot_date"].isoformat()
                if isinstance(s["snapshot_date"], date)
                else s["snapshot_date"]
            )
        client.table("daily_load_snapshots").upsert(
            snapshots, on_conflict="user_id,snapshot_date,sport"
        ).execute()

    async def get_latest_load(
        self, user_id: str, sport: str | None = None
    ) -> DailyLoadSnapshot | None:
        client = self._require_client()
        query = client.table("daily_load_snapshots").select("*").eq("user_id", user_id)
        query = query.is_("sport", "null") if sport is None else query.eq("sport", sport)
        response = query.order("snapshot_date", desc=True).limit(1).execute()
        rows = response.data or []
        if not rows:
            return None
        return DailyLoadSnapshot.model_validate(rows[0])

    # ── Goals ─────────────────────────────────────────────────

    async def create_goal(self, goal: Goal) -> Goal:
        client = self._require_client()
        payload = goal.model_dump(mode="json", exclude={"created_at", "updated_at"})
        if not payload.get("id"):
            payload["id"] = str(uuid4())
        response = client.table("goals").insert(payload).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the inserted goal row.")
        return Goal.model_validate(rows[0])

    async def list_active_goals(self, user_id: str) -> list[Goal]:
        client = self._require_client()
        response = (
            client.table("goals")
            .select("*")
            .eq("user_id", user_id)
            .eq("status", "active")
            .order("priority")
            .execute()
        )
        return [Goal.model_validate(r) for r in (response.data or [])]

    async def get_goal(self, goal_id: str, user_id: str) -> Goal:
        client = self._require_client()
        response = (
            client.table("goals").select("*").eq("id", goal_id).eq("user_id", user_id).execute()
        )
        rows = response.data or []
        if not rows:
            raise RecordNotFoundError(f"Goal '{goal_id}' not found.")
        return Goal.model_validate(rows[0])

    async def update_goal(self, goal_id: str, user_id: str, updates: dict) -> Goal:
        client = self._require_client()
        response = (
            client.table("goals").update(updates).eq("id", goal_id).eq("user_id", user_id).execute()
        )
        rows = response.data or []
        if not rows:
            raise RecordNotFoundError(f"Goal '{goal_id}' not found.")
        return Goal.model_validate(rows[0])

    # ── Recovery Logs ─────────────────────────────────────────

    async def upsert_recovery_log(self, log: RecoveryLog) -> RecoveryLog:
        client = self._require_client()
        payload = log.model_dump(mode="json", exclude={"created_at"})
        if not payload.get("id"):
            payload["id"] = str(uuid4())
        response = (
            client.table("recovery_logs").upsert(payload, on_conflict="user_id,log_date").execute()
        )
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the upserted recovery log row.")
        return RecoveryLog.model_validate(rows[0])

    async def list_recovery_logs(
        self, user_id: str, *, since: date | None = None, limit: int = 14
    ) -> list[RecoveryLog]:
        client = self._require_client()
        query = client.table("recovery_logs").select("*").eq("user_id", user_id)
        if since:
            query = query.gte("log_date", since.isoformat())
        response = query.order("log_date", desc=True).limit(limit).execute()
        return [RecoveryLog.model_validate(r) for r in (response.data or [])]

    # ── Schedule ──────────────────────────────────────────────

    async def upsert_schedule(self, schedule: ScheduleAvailability) -> ScheduleAvailability:
        client = self._require_client()
        payload = schedule.model_dump(mode="json", exclude={"created_at", "updated_at"})
        if not payload.get("id"):
            payload["id"] = str(uuid4())
        response = (
            client.table("schedule_availability").upsert(payload, on_conflict="user_id").execute()
        )
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the upserted schedule row.")
        return ScheduleAvailability.model_validate(rows[0])

    async def get_schedule(self, user_id: str) -> ScheduleAvailability | None:
        client = self._require_client()
        response = (
            client.table("schedule_availability").select("*").eq("user_id", user_id).execute()
        )
        rows = response.data or []
        return ScheduleAvailability.model_validate(rows[0]) if rows else None

    async def upsert_schedule_override(self, override: ScheduleOverride) -> ScheduleOverride:
        client = self._require_client()
        payload = override.model_dump(mode="json", exclude={"created_at"})
        if not payload.get("id"):
            payload["id"] = str(uuid4())
        response = (
            client.table("schedule_overrides")
            .upsert(payload, on_conflict="user_id,override_date")
            .execute()
        )
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the upserted schedule override row.")
        return ScheduleOverride.model_validate(rows[0])

    # ── Training Plans ────────────────────────────────────────

    async def create_training_plan(self, plan: TrainingPlan) -> TrainingPlan:
        client = self._require_client()
        # Supersede existing active plan
        client.table("training_plans").update({"status": "superseded"}).eq(
            "user_id", plan.user_id
        ).eq("status", "active").execute()

        payload = plan.model_dump(mode="json", exclude={"created_at", "updated_at"})
        if not payload.get("id"):
            payload["id"] = str(uuid4())
        response = client.table("training_plans").insert(payload).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the inserted training plan row.")
        return TrainingPlan.model_validate(rows[0])

    async def get_active_plan(self, user_id: str) -> TrainingPlan | None:
        client = self._require_client()
        response = (
            client.table("training_plans")
            .select("*")
            .eq("user_id", user_id)
            .eq("status", "active")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return TrainingPlan.model_validate(rows[0]) if rows else None

    async def create_plan_workouts(self, workouts: list[PlanWorkout]) -> list[PlanWorkout]:
        client = self._require_client()
        payloads = []
        for w in workouts:
            p = w.model_dump(mode="json", exclude={"created_at", "updated_at"})
            if not p.get("id"):
                p["id"] = str(uuid4())
            payloads.append(p)
        response = client.table("plan_workouts").insert(payloads).execute()
        return [PlanWorkout.model_validate(r) for r in (response.data or [])]

    async def list_plan_workouts(
        self, plan_id: str, *, since: date | None = None
    ) -> list[PlanWorkout]:
        client = self._require_client()
        query = client.table("plan_workouts").select("*").eq("plan_id", plan_id)
        if since:
            query = query.gte("workout_date", since.isoformat())
        response = query.order("workout_date").execute()
        return [PlanWorkout.model_validate(r) for r in (response.data or [])]

    # ── Chat (unchanged from original) ────────────────────────

    async def get_or_create_chat_thread(
        self, user_id: str, *, include_messages: bool = True
    ) -> ChatThread:
        client = self._require_client()
        response = client.table("chat_threads").select("*").eq("user_id", user_id).execute()
        rows = response.data or []
        if rows:
            thread = self._parse_chat_thread(rows[0])
        else:
            payload = {
                "id": str(uuid4()),
                "user_id": user_id,
                "state": {},
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
            }
            created = client.table("chat_threads").insert(payload).execute()
            created_rows = created.data or []
            if not created_rows:
                raise RuntimeError("Supabase did not return the inserted chat thread row.")
            thread = self._parse_chat_thread(created_rows[0])
        if not include_messages:
            return thread
        messages = await self.list_chat_messages(thread.id)
        return thread.model_copy(update={"messages": messages})

    async def update_chat_thread_state(self, thread_id: str, state: dict[str, Any]) -> ChatThread:
        client = self._require_client()
        response = (
            client.table("chat_threads").update({"state": state}).eq("id", thread_id).execute()
        )
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the updated chat thread row.")
        thread = self._parse_chat_thread(rows[0])
        messages = await self.list_chat_messages(thread.id)
        return thread.model_copy(update={"messages": messages})

    async def list_chat_messages(
        self,
        thread_id: str,
        *,
        limit: int = 50,
        before: tuple[datetime, str] | None = None,
    ) -> list[ChatMessage]:
        client = self._require_client()
        query = client.table("chat_messages").select("*").eq("thread_id", thread_id)
        if before is not None:
            created_at, message_id = before
            try:
                UUID(message_id)
            except ValueError as exc:
                raise ValueError("Invalid chat message cursor.") from exc
            timestamp = created_at.isoformat()
            query = query.or_(
                f"created_at.lt.{timestamp},and(created_at.eq.{timestamp},id.lt.{message_id})"
            )
        response = (
            query.order("created_at", desc=True).order("id", desc=True).limit(limit).execute()
        )
        rows = response.data or []
        return list(reversed([self._parse_chat_message(row) for row in rows]))

    async def create_chat_message(  # noqa: PLR0913
        self,
        *,
        thread_id: str,
        user_id: str,
        role: str,
        parts: list[MessagePart],
        metadata: dict[str, Any] | None = None,
        attachments: list[MessageAttachment] | None = None,
        message_id: str | None = None,
    ) -> ChatMessage:
        client = self._require_client()
        caller_supplied_id = message_id is not None
        message_id = message_id or str(uuid4())
        # `content` is denormalized plain text kept for one release window so
        # existing readers (exports, search) keep working until the follow-up
        # drop migration. Derive it from any text parts.
        content = "".join(str(part.get("text", "")) for part in parts if part.get("type") == "text")
        payload = {
            "id": message_id,
            "thread_id": thread_id,
            "user_id": user_id,
            "role": role,
            "content": content,
            "parts": parts,
            "attachments": attachments or [],
            "metadata": metadata or {},
            "created_at": datetime.now(UTC).isoformat(),
        }
        query = client.table("chat_messages")
        response = (
            query.upsert(payload, on_conflict="id", ignore_duplicates=True).execute()
            if caller_supplied_id
            else query.insert(payload).execute()
        )
        rows = response.data or []
        if caller_supplied_id and not rows:
            existing = (
                client.table("chat_messages")
                .select("*")
                .eq("id", message_id)
                .eq("user_id", user_id)
                .execute()
            )
            rows = existing.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the inserted chat message row.")
        return self._parse_chat_message(rows[0])

    async def get_or_create_chat_model_state(
        self, *, thread_id: str, user_id: str
    ) -> ChatModelState:
        client = self._require_client()
        response = (
            client.table("chat_model_states")
            .select("*")
            .eq("thread_id", thread_id)
            .eq("user_id", user_id)
            .execute()
        )
        rows = response.data or []
        if rows:
            return self._parse_chat_model_state(rows[0])
        now = datetime.now(UTC).isoformat()
        payload = {
            "thread_id": thread_id,
            "user_id": user_id,
            "items": [],
            "coaching_memory": [],
            "compaction_metadata": {},
            "schema_version": 1,
            "version": 0,
            "lease_id": None,
            "lease_expires_at": None,
            "created_at": now,
            "updated_at": now,
        }
        created = (
            client.table("chat_model_states")
            .upsert(payload, on_conflict="thread_id", ignore_duplicates=True)
            .execute()
        )
        created_rows = created.data or []
        if created_rows:
            return self._parse_chat_model_state(created_rows[0])
        concurrent = (
            client.table("chat_model_states")
            .select("*")
            .eq("thread_id", thread_id)
            .eq("user_id", user_id)
            .execute()
        )
        concurrent_rows = concurrent.data or []
        if not concurrent_rows:
            raise RuntimeError("Supabase did not return the chat model state row.")
        return self._parse_chat_model_state(concurrent_rows[0])

    async def replace_chat_model_state(
        self,
        *,
        thread_id: str,
        user_id: str,
        replacement: ChatModelStateReplaceRequest,
    ) -> ChatModelState:
        client = self._require_client()
        payload = {
            "items": replacement.items,
            "coaching_memory": replacement.coaching_memory,
            "compaction_metadata": replacement.compaction_metadata,
            "version": replacement.expected_version + 1,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        response = (
            client.table("chat_model_states")
            .update(payload)
            .eq("thread_id", thread_id)
            .eq("user_id", user_id)
            .eq("version", replacement.expected_version)
            .eq("lease_id", replacement.lease_id)
            .gt("lease_expires_at", datetime.now(UTC).isoformat())
            .execute()
        )
        rows = response.data or []
        if not rows:
            raise ValueError("Chat model state lease or version conflict.")
        return self._parse_chat_model_state(rows[0])

    async def acquire_chat_turn_lease(
        self,
        *,
        thread_id: str,
        user_id: str,
        lease_id: str,
        ttl_seconds: int,
    ) -> ChatModelState:
        # The cache miss can hit the SELECT, the UPSERT, or the version-checked UPDATE;
        # all are safe to re-run, so retry the whole operation rather than one query.
        async def _attempt() -> ChatModelState:
            current = await self.get_or_create_chat_model_state(
                thread_id=thread_id, user_id=user_id
            )
            now = datetime.now(UTC)
            if (
                current.lease_id is not None
                and current.lease_expires_at is not None
                and current.lease_expires_at > now
            ):
                if current.lease_id == lease_id:
                    return current
                raise ValueError("A chat turn is already in progress.")
            client = self._require_client()
            response = (
                client.table("chat_model_states")
                .update(
                    {
                        "lease_id": lease_id,
                        "lease_expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
                        "version": current.version + 1,
                        "updated_at": now.isoformat(),
                    }
                )
                .eq("thread_id", thread_id)
                .eq("user_id", user_id)
                .eq("version", current.version)
                .execute()
            )
            rows = response.data or []
            if not rows:
                raise ValueError("A chat turn is already in progress.")
            return self._parse_chat_model_state(rows[0])

        return await _with_schema_cache_retry(_attempt)

    async def release_chat_turn_lease(
        self, *, thread_id: str, user_id: str, lease_id: str
    ) -> ChatModelState:
        client = self._require_client()
        response = (
            client.table("chat_model_states")
            .update(
                {
                    "lease_id": None,
                    "lease_expires_at": None,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
            .eq("thread_id", thread_id)
            .eq("user_id", user_id)
            .eq("lease_id", lease_id)
            .execute()
        )
        rows = response.data or []
        if not rows:
            raise ValueError("Chat turn lease is no longer owned by this request.")
        return self._parse_chat_model_state(rows[0])

    # ── Internal helpers ──────────────────────────────────────

    def _build_client(self) -> Client | None:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            return None
        return create_client(settings.supabase_url, settings.supabase_service_role_key)

    def _require_client(self) -> Any:
        if self._client is None:
            raise RepositoryNotConfiguredError(
                "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY."
            )
        return self._client

    @staticmethod
    def _parse_chat_thread(row: object) -> ChatThread:
        if not isinstance(row, dict):
            raise TypeError("Supabase chat thread rows must be objects.")
        return ChatThread.model_validate(row)

    @staticmethod
    def _parse_chat_message(row: object) -> ChatMessage:
        if not isinstance(row, dict):
            raise TypeError("Supabase chat message rows must be objects.")
        return ChatMessage.model_validate(row)

    @staticmethod
    def _parse_chat_model_state(row: object) -> ChatModelState:
        if not isinstance(row, dict):
            raise TypeError("Supabase chat model state rows must be objects.")
        return ChatModelState.model_validate(row)
