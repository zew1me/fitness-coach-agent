from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import httpx
from postgrest.exceptions import APIError as PostgRESTAPIError

from backend.config import settings
from backend.models.auth import (
    OAuthAuthorizationCodeRecord,
    OAuthDeviceCodeRecord,
    OAuthGrantRecord,
    OAuthRefreshTokenRecord,
)
from backend.repos.oauth_repo_base import _OAuthRepositoryBase
from supabase import AsyncClient as SupabaseAsyncClient

logger = logging.getLogger(__name__)


class AsyncOAuthRepository(_OAuthRepositoryBase):
    """Asynchronous Supabase-backed OAuth persistence."""

    async def aclose(self) -> None:
        """Close the PostgREST HTTP connections used by this repository."""
        if self._client is not None:
            await self._client.postgrest.aclose()

    async def get_active_grant(
        self, *, user_id: str, client_id: str, redirect_uri: str
    ) -> OAuthGrantRecord | None:
        client = self._require_client()
        query = (
            client.table(self._grants_table)
            .select("*")
            .eq("user_id", user_id)
            .eq("client_id", client_id)
            .eq("redirect_uri", redirect_uri)
            .is_("revoked_at", "null")
            .limit(1)
        )
        response = await query.execute()
        rows = response.data or []
        if not rows:
            return None
        return self._parse_grant(rows[0])

    async def get_grant_by_id(self, grant_id: str) -> OAuthGrantRecord | None:
        client = self._require_client()
        query = client.table(self._grants_table).select("*").eq("id", grant_id).limit(1)
        response = await query.execute()
        rows = response.data or []
        if not rows:
            return None
        return self._parse_grant(rows[0])

    async def upsert_grant(
        self, *, user_id: str, client_id: str, redirect_uri: str, scopes: list[str]
    ) -> OAuthGrantRecord:
        for attempt in range(self._GATEWAY_RETRY_ATTEMPTS):
            try:
                return await self._upsert_grant_once(
                    user_id=user_id,
                    client_id=client_id,
                    redirect_uri=redirect_uri,
                    scopes=scopes,
                )
            except PostgRESTAPIError as exc:
                if not self._should_retry_gateway_error(exc, attempt):
                    raise
                await asyncio.sleep(self._GATEWAY_RETRY_BACKOFF_SECONDS)
        raise AssertionError("unreachable: OAuth grant retry loop exited without returning")

    async def _upsert_grant_once(
        self, *, user_id: str, client_id: str, redirect_uri: str, scopes: list[str]
    ) -> OAuthGrantRecord:
        existing = await self.get_active_grant(
            user_id=user_id, client_id=client_id, redirect_uri=redirect_uri
        )
        requested_scopes, merged_scopes = self._resolve_grant_scopes(existing, scopes)
        if existing is not None and existing.scopes == merged_scopes:
            return existing

        client = self._require_client()
        if existing is None:
            payload = self._grant_insert_payload(
                user_id=user_id,
                client_id=client_id,
                redirect_uri=redirect_uri,
                scopes=requested_scopes,
            )
            response = await client.table(self._grants_table).insert(payload).execute()
        else:
            payload = self._grant_update_payload(existing, requested_scopes)
            query = client.table(self._grants_table).update(payload).eq("id", existing.id)
            response = await query.execute()
        return self._parse_grant(self._require_grant_row(response.data or []))

    # This signature is permanently flat so the public boundary mirrors the OAuth protocol fields.
    async def create_authorization_code(  # noqa: PLR0913
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
        payload = self._authorization_code_payload(
            raw_code=raw_code,
            grant_id=grant_id,
            user_id=user_id,
            client_id=client_id,
            redirect_uri=redirect_uri,
            scopes=scopes,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
        )
        response = await client.table(self._authorization_codes_table).insert(payload).execute()
        self._require_authorization_code_row(response.data or [])
        return raw_code

    async def get_authorization_code(self, raw_code: str) -> OAuthAuthorizationCodeRecord | None:
        return await self._find_authorization_code_by_hash(raw_code)

    async def consume_authorization_code(self, raw_code: str) -> OAuthAuthorizationCodeRecord:
        client = self._require_client()
        record = await self._find_authorization_code_by_hash(raw_code)
        if record is None:
            raise ValueError("Invalid authorization code.")
        now = datetime.now(UTC)
        if record.consumed_at is not None or record.expires_at <= now:
            raise ValueError("Authorization code is no longer valid.")
        query = (
            client.table(self._authorization_codes_table)
            .update({"consumed_at": now.isoformat()})
            .eq("id", record.id)
        )
        response = await query.execute()
        row = self._require_consumed_authorization_code_row(response.data or [])
        return self._parse_authorization_code(row)

    async def create_refresh_token(
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
        payload = self._refresh_token_payload(
            raw_token=raw_token,
            grant_id=grant_id,
            user_id=user_id,
            client_id=client_id,
            scopes=scopes,
            rotated_from_id=rotated_from_id,
        )
        response = await client.table(self._refresh_tokens_table).insert(payload).execute()
        self._require_refresh_token_row(response.data or [])
        return raw_token

    async def get_refresh_token(self, raw_token: str) -> OAuthRefreshTokenRecord | None:
        return await self._find_refresh_token_by_hash(raw_token)

    async def rotate_refresh_token(self, raw_token: str) -> tuple[OAuthRefreshTokenRecord, str]:
        client = self._require_client()
        existing = await self._find_refresh_token_by_hash(raw_token)
        if existing is None:
            raise ValueError("Invalid refresh token.")
        now = datetime.now(UTC)
        if existing.revoked_at is not None or existing.expires_at <= now:
            raise ValueError("Refresh token is no longer valid.")
        query = (
            client.table(self._refresh_tokens_table)
            .update({"revoked_at": now.isoformat()})
            .eq("id", existing.id)
        )
        response = await query.execute()
        row = self._require_revoked_refresh_token_row(response.data or [])
        revoked = self._parse_refresh_token(row)
        replacement = await self.create_refresh_token(
            grant_id=revoked.grant_id,
            user_id=revoked.user_id,
            client_id=revoked.client_id,
            scopes=revoked.scopes,
            rotated_from_id=revoked.id,
        )
        return revoked, replacement

    async def revoke_refresh_token(self, raw_token: str) -> bool:
        client = self._require_client()
        existing = await self._find_refresh_token_by_hash(raw_token)
        if existing is None or existing.revoked_at is not None:
            return False
        query = (
            client.table(self._refresh_tokens_table)
            .update(self._revocation_payload())
            .eq("id", existing.id)
        )
        response = await query.execute()
        return bool(response.data or [])

    async def revoke_grant(self, grant_id: str) -> bool:
        client = self._require_client()
        payload = self._revocation_payload()
        grant_query = client.table(self._grants_table).update(payload).eq("id", grant_id)
        grant_response = await grant_query.execute()
        refresh_query = (
            client.table(self._refresh_tokens_table)
            .update(payload)
            .eq("grant_id", grant_id)
            .is_("revoked_at", "null")
        )
        await refresh_query.execute()
        return bool(grant_response.data or [])

    # ── RFC 8628 device authorization ──────────────────────────
    # Status transitions are conditional updates (``status=eq.<expected>``) returning the row,
    # so concurrent approvals, denials and polls cannot both win: an empty response means the
    # row had already moved on (or expired), and callers treat that as an invalid code.

    async def create_device_code(
        self, *, client_id: str, scopes: list[str], expires_at: datetime, interval_seconds: int
    ) -> tuple[str, OAuthDeviceCodeRecord]:
        """Insert a pending device authorization; return the raw device code and the row."""
        client = self._require_client()
        for attempt in range(self._DEVICE_CODE_INSERT_ATTEMPTS):
            raw_device_code = self._issue_device_code()
            payload = self._device_code_payload(
                raw_device_code=raw_device_code,
                client_id=client_id,
                scopes=scopes,
                expires_at=expires_at,
                interval_seconds=interval_seconds,
            )
            try:
                response = await client.table(self._device_codes_table).insert(payload).execute()
            except PostgRESTAPIError as exc:
                if not self._should_retry_device_code_insert(exc, attempt):
                    raise
                continue
            record = self._parse_device_code(self._require_device_code_row(response.data or []))
            await self._purge_expired_device_codes()
            return raw_device_code, record
        raise AssertionError("unreachable: device code insert loop exited without returning")

    async def find_pending_device_code(self, user_code: str) -> OAuthDeviceCodeRecord | None:
        """Look up a pending device authorization by its normalised user code."""
        client = self._require_client()
        query = (
            client.table(self._device_codes_table)
            .select("*")
            .eq("user_code", user_code)
            .eq("status", "pending")
            .limit(1)
        )
        response = await query.execute()
        return self._parse_optional_device_code(response.data or [])

    async def get_device_code(self, raw_device_code: str) -> OAuthDeviceCodeRecord | None:
        """Look up a device authorization by the raw device code the client presents."""
        client = self._require_client()
        query = (
            client.table(self._device_codes_table)
            .select("*")
            .eq("device_code_hash", self._hash_secret(raw_device_code))
            .limit(1)
        )
        response = await query.execute()
        return self._parse_optional_device_code(response.data or [])

    async def record_device_code_poll(
        self,
        record_id: str,
        *,
        observed_polled_at: datetime | None,
        polled_at: datetime,
        interval_seconds: int,
    ) -> bool:
        """Stamp a token poll on a pending row, raising its interval after ``slow_down``.

        The update only applies while ``last_polled_at`` still holds the value this poll read
        (compare-and-set), so a stale concurrent poll can never overwrite a newer, larger
        backoff. Returns ``False`` when another poll got there first.
        """
        client = self._require_client()
        query = (
            client.table(self._device_codes_table)
            .update({"last_polled_at": polled_at.isoformat(), "interval_seconds": interval_seconds})
            .eq("id", record_id)
            .eq("status", "pending")
        )
        if observed_polled_at is None:
            query = query.is_("last_polled_at", "null")
        else:
            query = query.eq("last_polled_at", observed_polled_at.isoformat())
        response = await query.execute()
        return bool(response.data)

    async def approve_device_code(
        self, record_id: str, *, user_id: str, grant_id: str
    ) -> OAuthDeviceCodeRecord | None:
        """Move an unexpired pending row to ``approved``; ``None`` if it was not pending."""
        client = self._require_client()
        now = datetime.now(UTC).isoformat()
        query = (
            client.table(self._device_codes_table)
            .update(
                {"status": "approved", "user_id": user_id, "grant_id": grant_id, "approved_at": now}
            )
            .eq("id", record_id)
            .eq("status", "pending")
            .gt("expires_at", now)
        )
        response = await query.execute()
        return self._parse_optional_device_code(response.data or [])

    async def deny_device_code(self, record_id: str) -> bool:
        """Move an unexpired pending row to ``denied``; ``False`` if it was not pending."""
        client = self._require_client()
        query = (
            client.table(self._device_codes_table)
            .update({"status": "denied"})
            .eq("id", record_id)
            .eq("status", "pending")
            .gt("expires_at", datetime.now(UTC).isoformat())
        )
        response = await query.execute()
        return bool(response.data or [])

    async def consume_device_code(self, record_id: str) -> OAuthDeviceCodeRecord | None:
        """Atomically move an unexpired approved row to ``consumed``.

        The ``status=eq.approved`` filter makes this the single winner among concurrent
        polls, so the token bundle is issued at most once per device code.
        """
        client = self._require_client()
        query = (
            client.table(self._device_codes_table)
            .update({"status": "consumed"})
            .eq("id", record_id)
            .eq("status", "approved")
            .gt("expires_at", datetime.now(UTC).isoformat())
        )
        response = await query.execute()
        return self._parse_optional_device_code(response.data or [])

    async def _purge_expired_device_codes(self) -> None:
        """Best-effort delete of rows that expired more than the retention window ago.

        Housekeeping must never fail the athlete's login, so database and transport errors
        are logged and swallowed here; the next successful purge removes the same rows.
        """
        client = self._require_client()
        cutoff = datetime.now(UTC) - self._DEVICE_CODE_RETENTION
        try:
            await (
                client.table(self._device_codes_table)
                .delete()
                .lt("expires_at", cutoff.isoformat())
                .execute()
            )
        except (PostgRESTAPIError, httpx.HTTPError) as exc:
            logger.warning("oauth device code purge failed error_type=%s", type(exc).__name__)

    async def _find_authorization_code_by_hash(
        self, raw_code: str
    ) -> OAuthAuthorizationCodeRecord | None:
        client = self._require_client()
        query = (
            client.table(self._authorization_codes_table)
            .select("*")
            .eq("token_hash", self._hash_secret(raw_code))
            .limit(1)
        )
        response = await query.execute()
        rows = response.data or []
        if not rows:
            return None
        return self._parse_authorization_code(rows[0])

    async def _find_refresh_token_by_hash(self, raw_token: str) -> OAuthRefreshTokenRecord | None:
        client = self._require_client()
        query = (
            client.table(self._refresh_tokens_table)
            .select("*")
            .eq("token_hash", self._hash_secret(raw_token))
            .limit(1)
        )
        response = await query.execute()
        rows = response.data or []
        if not rows:
            return None
        return self._parse_refresh_token(rows[0])

    def _build_client(self) -> SupabaseAsyncClient | None:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            return None
        return SupabaseAsyncClient(settings.supabase_url, settings.supabase_service_role_key)

    def _require_client(self) -> SupabaseAsyncClient:
        return super()._require_client()
