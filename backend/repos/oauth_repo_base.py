from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

from postgrest.types import JSON

from backend.models.auth import (
    OAuthAuthorizationCodeRecord,
    OAuthGrantRecord,
    OAuthRefreshTokenRecord,
)


class OAuthRepositoryNotConfiguredError(RuntimeError):
    """Raised when OAuth persistence is requested without Supabase config."""


class _OAuthRepositoryBase:
    """Share configuration and pure helpers across OAuth repository runtimes."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        grants_table: str = "oauth_grants",
        authorization_codes_table: str = "oauth_authorization_codes",
        refresh_tokens_table: str = "oauth_refresh_tokens",
    ) -> None:
        """Configure an optional client and the three OAuth persistence tables."""
        self._client = client or self._build_client()
        self._grants_table = grants_table
        self._authorization_codes_table = authorization_codes_table
        self._refresh_tokens_table = refresh_tokens_table

    def _build_client(self) -> Any | None:
        """Build the runtime-specific Supabase client when configuration is present."""
        raise NotImplementedError

    def _require_client(self) -> Any:
        """Return the configured client or raise the shared configuration error."""
        if self._client is None:
            raise OAuthRepositoryNotConfiguredError(
                "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY."
            )
        return self._client

    @staticmethod
    def _resolve_grant_scopes(
        existing: OAuthGrantRecord | None, scopes: list[str]
    ) -> tuple[list[str], list[str]]:
        """Canonicalize requested scopes and merge them with an existing grant."""
        requested_scopes = sorted(set(scopes))
        merged_scopes = (
            sorted(set(existing.scopes).union(requested_scopes))
            if existing is not None
            else requested_scopes
        )
        return requested_scopes, merged_scopes

    @staticmethod
    def _grant_insert_payload(
        *, user_id: str, client_id: str, redirect_uri: str, scopes: list[str]
    ) -> dict[str, JSON]:
        """Build a new active-grant row with generated identity and timestamps."""
        now = datetime.now(UTC).isoformat()
        return {
            "id": str(uuid4()),
            "user_id": user_id,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scopes": scopes,
            "created_at": now,
            "updated_at": now,
            "revoked_at": None,
        }

    @staticmethod
    def _grant_update_payload(existing: OAuthGrantRecord, scopes: list[str]) -> dict[str, JSON]:
        """Build an update that preserves existing scopes and reactivates the grant."""
        return {
            "scopes": sorted(set(existing.scopes).union(scopes)),
            "updated_at": datetime.now(UTC).isoformat(),
            "revoked_at": None,
        }

    # These signatures intentionally remain flat and permanent: each protocol field maps directly
    # to a persisted authorization-code column, and grouping them would obscure that contract.
    def _authorization_code_payload(  # noqa: PLR0913
        self,
        *,
        raw_code: str,
        grant_id: str,
        user_id: str,
        client_id: str,
        redirect_uri: str,
        scopes: list[str],
        code_challenge: str,
        code_challenge_method: str,
    ) -> dict[str, JSON]:
        """Hash a raw authorization code and build its short-lived persistence row."""
        return {
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

    # This signature is also intentionally permanent: each ownership field maps directly to a
    # refresh-token column, and grouping the fields would obscure the persistence contract.
    def _refresh_token_payload(  # noqa: PLR0913
        self,
        *,
        raw_token: str,
        grant_id: str,
        user_id: str,
        client_id: str,
        scopes: list[str],
        rotated_from_id: str | None,
    ) -> dict[str, JSON]:
        """Hash a raw refresh token and build its expiring persistence row."""
        return {
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

    @staticmethod
    def _revocation_payload() -> dict[str, JSON]:
        """Build a revocation timestamp update."""
        return {"revoked_at": datetime.now(UTC).isoformat()}

    @staticmethod
    def _require_grant_row(rows: Sequence[object]) -> object:
        """Return the first written grant row, rejecting an empty response."""
        if not rows:
            raise RuntimeError("Supabase did not return the OAuth grant row.")
        return rows[0]

    @staticmethod
    def _require_authorization_code_row(rows: Sequence[object]) -> object:
        """Return the first written authorization-code row, rejecting an empty response."""
        if not rows:
            raise RuntimeError("Supabase did not return the OAuth authorization code row.")
        return rows[0]

    @staticmethod
    def _require_consumed_authorization_code_row(rows: Sequence[object]) -> object:
        """Return the consumed code row, rejecting an empty update response."""
        if not rows:
            raise RuntimeError("Supabase did not return the consumed OAuth code row.")
        return rows[0]

    @staticmethod
    def _require_refresh_token_row(rows: Sequence[object]) -> object:
        """Return the first written refresh-token row, rejecting an empty response."""
        if not rows:
            raise RuntimeError("Supabase did not return the OAuth refresh token row.")
        return rows[0]

    @staticmethod
    def _require_revoked_refresh_token_row(rows: Sequence[object]) -> object:
        """Return the revoked token row, rejecting an empty update response."""
        if not rows:
            raise RuntimeError("Supabase did not return the revoked OAuth refresh token row.")
        return rows[0]

    @staticmethod
    def _issue_secret() -> str:
        """Issue a 256-bit random OAuth credential as hexadecimal text."""
        return uuid4().hex + uuid4().hex

    @staticmethod
    def _hash_secret(raw_secret: str) -> str:
        """Hash a credential for lookup without persisting the bearer secret."""
        return sha256(raw_secret.encode("utf-8")).hexdigest()

    @staticmethod
    def _parse_grant(row: object) -> OAuthGrantRecord:
        """Validate an untyped PostgREST row as an OAuth grant."""
        if not isinstance(row, dict):
            raise TypeError("Supabase OAuth grant rows must be objects.")
        return OAuthGrantRecord.model_validate(row)

    @staticmethod
    def _parse_authorization_code(row: object) -> OAuthAuthorizationCodeRecord:
        """Validate an untyped PostgREST row as an authorization code."""
        if not isinstance(row, dict):
            raise TypeError("Supabase OAuth authorization code rows must be objects.")
        return OAuthAuthorizationCodeRecord.model_validate(row)

    @staticmethod
    def _parse_refresh_token(row: object) -> OAuthRefreshTokenRecord:
        """Validate an untyped PostgREST row as a refresh token."""
        if not isinstance(row, dict):
            raise TypeError("Supabase OAuth refresh token rows must be objects.")
        return OAuthRefreshTokenRecord.model_validate(row)
