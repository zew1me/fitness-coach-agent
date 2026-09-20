from __future__ import annotations

from datetime import UTC, datetime

from backend.config import settings
from backend.models.auth import (
    OAuthAuthorizationCodeRecord,
    OAuthGrantRecord,
    OAuthRefreshTokenRecord,
)
from backend.repos.oauth_repo_base import _OAuthRepositoryBase
from supabase import AsyncClient as SupabaseAsyncClient


class AsyncOAuthRepository(_OAuthRepositoryBase):
    """Asynchronous Supabase-backed OAuth persistence."""

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
        existing = await self.get_active_grant(
            user_id=user_id, client_id=client_id, redirect_uri=redirect_uri
        )
        client = self._require_client()
        if existing is None:
            payload = self._grant_insert_payload(
                user_id=user_id,
                client_id=client_id,
                redirect_uri=redirect_uri,
                scopes=scopes,
            )
            response = await client.table(self._grants_table).insert(payload).execute()
        else:
            payload = self._grant_update_payload(existing, scopes)
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
