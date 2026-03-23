from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

from backend.config import settings
from backend.models.auth import (
    OAuthAuthorizationCodeRecord,
    OAuthGrantRecord,
    OAuthRefreshTokenRecord,
)
from supabase import Client, create_client


class OAuthRepositoryNotConfiguredError(RuntimeError):
    """Raised when OAuth persistence is requested without Supabase config."""


class OAuthRepository:
    """Supabase-backed persistence for durable OAuth grants, codes, and refresh tokens."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        grants_table: str = "oauth_grants",
        authorization_codes_table: str = "oauth_authorization_codes",
        refresh_tokens_table: str = "oauth_refresh_tokens",
    ) -> None:
        self._client = client or self._build_client()
        self._grants_table = grants_table
        self._authorization_codes_table = authorization_codes_table
        self._refresh_tokens_table = refresh_tokens_table

    def get_active_grant(
        self, *, user_id: str, client_id: str, redirect_uri: str
    ) -> OAuthGrantRecord | None:
        client = self._require_client()
        response = (
            client.table(self._grants_table)
            .select("*")
            .eq("user_id", user_id)
            .eq("client_id", client_id)
            .eq("redirect_uri", redirect_uri)
            .is_("revoked_at", "null")
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        return self._parse_grant(rows[0])

    def get_grant_by_id(self, grant_id: str) -> OAuthGrantRecord | None:
        client = self._require_client()
        response = (
            client.table(self._grants_table).select("*").eq("id", grant_id).limit(1).execute()
        )
        rows = response.data or []
        if not rows:
            return None
        return self._parse_grant(rows[0])

    def upsert_grant(
        self, *, user_id: str, client_id: str, redirect_uri: str, scopes: list[str]
    ) -> OAuthGrantRecord:
        existing = self.get_active_grant(
            user_id=user_id, client_id=client_id, redirect_uri=redirect_uri
        )
        client = self._require_client()
        now = datetime.now(UTC).isoformat()
        if existing is None:
            payload = {
                "id": str(uuid4()),
                "user_id": user_id,
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scopes": scopes,
                "created_at": now,
                "updated_at": now,
                "revoked_at": None,
            }
            response = client.table(self._grants_table).insert(payload).execute()
        else:
            payload = {
                "scopes": sorted(set(existing.scopes).union(scopes)),
                "updated_at": now,
                "revoked_at": None,
            }
            response = (
                client.table(self._grants_table).update(payload).eq("id", existing.id).execute()
            )
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the OAuth grant row.")
        return self._parse_grant(rows[0])

    def create_authorization_code(  # noqa: PLR0913
        self,
        *,
        grant_id: str,
        user_id: str,
        client_id: str,
        redirect_uri: str,
        scopes: list[str],
        code_challenge: str,
        code_challenge_method: str,
    ) -> str:
        client = self._require_client()
        raw_code = self._issue_secret()
        payload = {
            "id": str(uuid4()),
            "grant_id": grant_id,
            "user_id": user_id,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scopes": scopes,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "token_hash": self._hash_secret(raw_code),
            "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            "consumed_at": None,
            "created_at": datetime.now(UTC).isoformat(),
        }
        response = client.table(self._authorization_codes_table).insert(payload).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the OAuth authorization code row.")
        return raw_code

    def consume_authorization_code(self, raw_code: str) -> OAuthAuthorizationCodeRecord:
        client = self._require_client()
        record = self._find_authorization_code_by_hash(raw_code)
        if record is None:
            raise ValueError("Invalid authorization code.")
        now = datetime.now(UTC)
        if record.consumed_at is not None or record.expires_at <= now:
            raise ValueError("Authorization code is no longer valid.")
        response = (
            client.table(self._authorization_codes_table)
            .update({"consumed_at": now.isoformat()})
            .eq("id", record.id)
            .execute()
        )
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the consumed OAuth code row.")
        return self._parse_authorization_code(rows[0])

    def create_refresh_token(
        self,
        *,
        grant_id: str,
        user_id: str,
        client_id: str,
        scopes: list[str],
        rotated_from_id: str | None = None,
    ) -> str:
        client = self._require_client()
        raw_token = self._issue_secret()
        payload = {
            "id": str(uuid4()),
            "grant_id": grant_id,
            "user_id": user_id,
            "client_id": client_id,
            "scopes": scopes,
            "token_hash": self._hash_secret(raw_token),
            "expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            "revoked_at": None,
            "created_at": datetime.now(UTC).isoformat(),
            "rotated_from_id": rotated_from_id,
        }
        response = client.table(self._refresh_tokens_table).insert(payload).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the OAuth refresh token row.")
        return raw_token

    def rotate_refresh_token(self, raw_token: str) -> tuple[OAuthRefreshTokenRecord, str]:
        client = self._require_client()
        existing = self._find_refresh_token_by_hash(raw_token)
        if existing is None:
            raise ValueError("Invalid refresh token.")
        now = datetime.now(UTC)
        if existing.revoked_at is not None or existing.expires_at <= now:
            raise ValueError("Refresh token is no longer valid.")
        response = (
            client.table(self._refresh_tokens_table)
            .update({"revoked_at": now.isoformat()})
            .eq("id", existing.id)
            .execute()
        )
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the revoked OAuth refresh token row.")
        revoked = self._parse_refresh_token(rows[0])
        replacement = self.create_refresh_token(
            grant_id=revoked.grant_id,
            user_id=revoked.user_id,
            client_id=revoked.client_id,
            scopes=revoked.scopes,
            rotated_from_id=revoked.id,
        )
        return revoked, replacement

    def revoke_refresh_token(self, raw_token: str) -> bool:
        client = self._require_client()
        existing = self._find_refresh_token_by_hash(raw_token)
        if existing is None or existing.revoked_at is not None:
            return False
        response = (
            client.table(self._refresh_tokens_table)
            .update({"revoked_at": datetime.now(UTC).isoformat()})
            .eq("id", existing.id)
            .execute()
        )
        rows = response.data or []
        return bool(rows)

    def revoke_grant(self, grant_id: str) -> bool:
        client = self._require_client()
        now = datetime.now(UTC).isoformat()
        grant_response = (
            client.table(self._grants_table)
            .update({"revoked_at": now})
            .eq("id", grant_id)
            .execute()
        )
        client.table(self._refresh_tokens_table).update({"revoked_at": now}).eq(
            "grant_id", grant_id
        ).is_("revoked_at", "null").execute()
        return bool(grant_response.data or [])

    def _find_authorization_code_by_hash(
        self, raw_code: str
    ) -> OAuthAuthorizationCodeRecord | None:
        client = self._require_client()
        response = (
            client.table(self._authorization_codes_table)
            .select("*")
            .eq("token_hash", self._hash_secret(raw_code))
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        return self._parse_authorization_code(rows[0])

    def _find_refresh_token_by_hash(self, raw_token: str) -> OAuthRefreshTokenRecord | None:
        client = self._require_client()
        response = (
            client.table(self._refresh_tokens_table)
            .select("*")
            .eq("token_hash", self._hash_secret(raw_token))
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        return self._parse_refresh_token(rows[0])

    def _build_client(self) -> Client | None:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            return None
        return create_client(settings.supabase_url, settings.supabase_service_role_key)

    def _require_client(self) -> Any:
        if self._client is None:
            raise OAuthRepositoryNotConfiguredError(
                "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY."
            )
        return self._client

    @staticmethod
    def _issue_secret() -> str:
        return uuid4().hex + uuid4().hex

    @staticmethod
    def _hash_secret(raw_secret: str) -> str:
        return sha256(raw_secret.encode("utf-8")).hexdigest()

    @staticmethod
    def _parse_grant(row: object) -> OAuthGrantRecord:
        if not isinstance(row, dict):
            raise TypeError("Supabase OAuth grant rows must be objects.")
        return OAuthGrantRecord.model_validate(row)

    @staticmethod
    def _parse_authorization_code(row: object) -> OAuthAuthorizationCodeRecord:
        if not isinstance(row, dict):
            raise TypeError("Supabase OAuth authorization code rows must be objects.")
        return OAuthAuthorizationCodeRecord.model_validate(row)

    @staticmethod
    def _parse_refresh_token(row: object) -> OAuthRefreshTokenRecord:
        if not isinstance(row, dict):
            raise TypeError("Supabase OAuth refresh token rows must be objects.")
        return OAuthRefreshTokenRecord.model_validate(row)
