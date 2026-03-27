from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from backend.config import settings
from backend.models.planning import AthleteProfile, CheckInInput, CheckInRecord
from supabase import Client, create_client


class RepositoryNotConfiguredError(RuntimeError):
    """Raised when database-backed operations are requested without Supabase config."""


class RecordNotFoundError(LookupError):
    """Raised when a requested record is absent in persistence."""


class SupabaseRepository:
    """Supabase-backed adapter for athlete profile and check-in persistence."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        athlete_profiles_table: str = "athlete_profiles",
        check_ins_table: str = "check_ins",
    ) -> None:
        self._client = client or self._build_client()
        self._athlete_profiles_table = athlete_profiles_table
        self._check_ins_table = check_ins_table

    async def get_athlete_profile(self, user_id: str) -> AthleteProfile:
        client = self._require_client()
        response = (
            client.table(self._athlete_profiles_table).select("*").eq("user_id", user_id).execute()
        )
        rows = response.data or []
        if not rows:
            raise RecordNotFoundError(f"No athlete profile found for user '{user_id}'.")
        return self._parse_athlete_profile(rows[0])

    async def upsert_athlete_profile(self, profile: AthleteProfile) -> AthleteProfile:
        client = self._require_client()
        payload = profile.model_dump(mode="python")
        response = (
            client.table(self._athlete_profiles_table)
            .upsert(payload, on_conflict="user_id")
            .execute()
        )
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the upserted athlete profile row.")
        return self._parse_athlete_profile(rows[0])

    async def create_check_in(self, check_in: CheckInInput) -> CheckInRecord:
        client = self._require_client()
        payload: dict[str, Any] = {
            "id": str(uuid4()),
            "user_id": check_in.user_id,
            "raw_text": check_in.raw_text,
            "image_count": check_in.image_count,
            "effective_date": (
                check_in.effective_date.isoformat() if check_in.effective_date is not None else None
            ),
            "created_at": datetime.now(UTC).isoformat(),
        }
        response = client.table(self._check_ins_table).insert(payload).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the inserted check-in row.")
        return self._parse_check_in_record(rows[0])

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
    def _parse_athlete_profile(row: object) -> AthleteProfile:
        if not isinstance(row, dict):
            raise TypeError("Supabase athlete profile rows must be objects.")
        return AthleteProfile.model_validate(row)

    @staticmethod
    def _parse_check_in_record(row: object) -> CheckInRecord:
        if not isinstance(row, dict):
            raise TypeError("Supabase check-in rows must be objects.")
        return CheckInRecord.model_validate(row)
