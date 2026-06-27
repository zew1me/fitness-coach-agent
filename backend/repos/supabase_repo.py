from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

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
        payload = activity.model_dump(mode="json", exclude={"created_at", "updated_at"})
        if not payload.get("id"):
            payload["id"] = str(uuid4())
        response = client.table("activities").insert(payload).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the inserted activity row.")
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

    async def update_goal(self, goal_id: str, updates: dict) -> Goal:
        client = self._require_client()
        response = client.table("goals").update(updates).eq("id", goal_id).execute()
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

    async def replace_chat_model_state(  # noqa: PLR0913
        self,
        *,
        thread_id: str,
        user_id: str,
        expected_version: int,
        lease_id: str,
        items: list[dict[str, Any]],
        coaching_memory: list[dict[str, Any]],
        compaction_metadata: dict[str, Any],
    ) -> ChatModelState:
        client = self._require_client()
        payload = {
            "items": items,
            "coaching_memory": coaching_memory,
            "compaction_metadata": compaction_metadata,
            "version": expected_version + 1,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        response = (
            client.table("chat_model_states")
            .update(payload)
            .eq("thread_id", thread_id)
            .eq("user_id", user_id)
            .eq("version", expected_version)
            .eq("lease_id", lease_id)
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
        current = await self.get_or_create_chat_model_state(thread_id=thread_id, user_id=user_id)
        now = datetime.now(UTC)
        if (
            current.lease_id is not None
            and current.lease_id != lease_id
            and current.lease_expires_at is not None
            and current.lease_expires_at > now
        ):
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
