from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.config import settings
from backend.models.strava import (
    StravaConnectionCreate,
    StravaConnectionRecord,
    StravaTokenRotation,
)
from supabase import Client, create_client


class StravaRepositoryNotConfiguredError(RuntimeError):
    """Raised when Strava persistence is requested without Supabase config."""


class StravaRepository:
    """Supabase-backed persistence for a user's Strava connection lifecycle."""

    def __init__(self, client: Any | None = None, *, table: str = "strava_connections") -> None:
        self._client = client or self._build_client()
        self._table = table

    def get_active_connection(self, user_id: str) -> StravaConnectionRecord | None:
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

    def replace_connection(self, connection: StravaConnectionCreate) -> StravaConnectionRecord:
        client = self._require_client()
        response = client.rpc(
            "replace_strava_connection",
            {
                "p_user_id": connection.user_id,
                "p_strava_athlete_id": connection.strava_athlete_id,
                "p_strava_athlete_name": connection.strava_athlete_name,
                "p_scopes": connection.scopes,
                "p_access_token_ciphertext": connection.access_token_ciphertext,
                "p_refresh_token_ciphertext": connection.refresh_token_ciphertext,
                "p_token_type": connection.token_type,
                "p_expires_at": connection.expires_at.isoformat(),
                "p_authorization_version": connection.authorization_version,
            },
        ).execute()
        # RPC returns a single composite `strava_connections` row (JSON object).
        row = response.data
        if not row:
            raise RuntimeError("Supabase did not return the Strava connection row.")
        return self._parse_connection(row)

    def rotate_tokens(
        self,
        *,
        connection_id: str,
        expected_expires_at: datetime,
        rotation: StravaTokenRotation,
    ) -> StravaConnectionRecord | None:
        """Persist rotated tokens; returns ``None`` when the CAS lost to a newer rotation."""
        client = self._require_client()
        response = client.rpc(
            "rotate_strava_tokens",
            {
                "p_connection_id": connection_id,
                "p_expected_expires_at": expected_expires_at.isoformat(),
                "p_access_token_ciphertext": rotation.access_token_ciphertext,
                "p_refresh_token_ciphertext": rotation.refresh_token_ciphertext,
                "p_token_type": rotation.token_type,
                "p_expires_at": rotation.expires_at.isoformat(),
            },
        ).execute()
        row = response.data
        if not row:
            return None
        return self._parse_connection(row)

    def touch_last_sync(self, user_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        self._require_client().table(self._table).update({"last_sync_at": now}).eq(
            "user_id", user_id
        ).is_("revoked_at", "null").execute()

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
            raise StravaRepositoryNotConfiguredError(
                "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY."
            )
        return self._client

    @staticmethod
    def _parse_connection(row: object) -> StravaConnectionRecord:
        if not isinstance(row, dict):
            raise TypeError("Supabase Strava connection rows must be objects.")
        return StravaConnectionRecord.model_validate(row)
