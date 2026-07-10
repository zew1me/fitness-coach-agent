from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from backend.config import settings
from backend.models.intervals import IntervalsConnectionCreate, IntervalsConnectionRecord
from supabase import Client, create_client


class IntervalsRepositoryNotConfiguredError(RuntimeError):
    """Raised when Intervals persistence is requested without Supabase config."""


class IntervalsRepository:
    """Supabase-backed persistence for user Intervals.icu connections."""

    def __init__(self, client: Any | None = None, *, table: str = "intervals_connections") -> None:
        self._client = client or self._build_client()
        self._table = table

    def get_active_connection(self, user_id: str) -> IntervalsConnectionRecord | None:
        response = (
            self._require_client()
            .table(self._table)
            .select("*")
            .eq("user_id", user_id)
            .is_("revoked_at", "null")
            .order("connected_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        return self._parse_connection(rows[0])

    def replace_connection(
        self, connection: IntervalsConnectionCreate
    ) -> IntervalsConnectionRecord:
        client = self._require_client()
        now = datetime.now(UTC).isoformat()
        (
            client.table(self._table)
            .update({"revoked_at": now, "updated_at": now})
            .eq("user_id", connection.user_id)
            .is_("revoked_at", "null")
            .execute()
        )
        payload = {
            "id": str(uuid4()),
            "user_id": connection.user_id,
            "intervals_athlete_id": connection.intervals_athlete_id,
            "intervals_athlete_name": connection.intervals_athlete_name,
            "scopes": connection.scopes,
            "access_token_ciphertext": connection.access_token_ciphertext,
            "token_type": connection.token_type,
            "connected_at": now,
            "updated_at": now,
            "revoked_at": None,
        }
        response = client.table(self._table).insert(payload).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the Intervals connection row.")
        return self._parse_connection(rows[0])

    def revoke_active_connection(self, user_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        response = (
            self._require_client()
            .table(self._table)
            .update({"revoked_at": now, "updated_at": now})
            .eq("user_id", user_id)
            .is_("revoked_at", "null")
            .execute()
        )
        return bool(response.data or [])

    def _build_client(self) -> Client | None:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            return None
        return create_client(settings.supabase_url, settings.supabase_service_role_key)

    def _require_client(self) -> Any:
        if self._client is None:
            raise IntervalsRepositoryNotConfiguredError(
                "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY."
            )
        return self._client

    @staticmethod
    def _parse_connection(row: object) -> IntervalsConnectionRecord:
        if not isinstance(row, dict):
            raise TypeError("Supabase Intervals connection rows must be objects.")
        return IntervalsConnectionRecord.model_validate(row)
