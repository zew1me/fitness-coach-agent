from __future__ import annotations

import secrets
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

from postgrest.exceptions import APIError as PostgRESTAPIError
from postgrest.types import JSON

from backend.models.auth import (
    DEVICE_USER_CODE_ALPHABET,
    DEVICE_USER_CODE_LENGTH,
    OAuthAuthorizationCodeRecord,
    OAuthDeviceCodeRecord,
    OAuthGrantRecord,
    OAuthRefreshTokenRecord,
)


class OAuthRepositoryNotConfiguredError(RuntimeError):
    """Raised when OAuth persistence is requested without Supabase config."""


class _OAuthRepositoryBase:
    """Keep OAuth configuration and pure payload/validation helpers separate from I/O."""

    # A gateway timeout can hide a committed write. Retry the complete read-then-write
    # grant operation, allowing the next read to observe it.
    _GATEWAY_RETRYABLE_CODES = frozenset({"502", "503", "504"})
    _GATEWAY_RETRY_ATTEMPTS = 2
    _GATEWAY_RETRY_BACKOFF_SECONDS = 0.25
    # A fresh user code collides with a pending one with probability ~pending/20^8; retrying
    # with new codes makes a failure a practical impossibility without an unbounded loop.
    _UNIQUE_VIOLATION_CODE = "23505"
    _DEVICE_CODE_INSERT_ATTEMPTS = 3
    # Expired rows are kept for a day so a late poll still reads ``expired_token`` rather
    # than an unknown code, then purged opportunistically whenever a new flow starts.
    _DEVICE_CODE_RETENTION = timedelta(days=1)

    def __init__(
        self,
        client: Any | None = None,
        *,
        grants_table: str = "oauth_grants",
        authorization_codes_table: str = "oauth_authorization_codes",
        refresh_tokens_table: str = "oauth_refresh_tokens",
        device_codes_table: str = "oauth_device_codes",
    ) -> None:
        """Configure an optional client and the OAuth persistence tables."""
        self._client = client or self._build_client()
        self._grants_table = grants_table
        self._authorization_codes_table = authorization_codes_table
        self._refresh_tokens_table = refresh_tokens_table
        self._device_codes_table = device_codes_table

    def _build_client(self) -> Any | None:
        """Build the concrete repository's client when configuration is present."""
        raise NotImplementedError

    def _require_client(self) -> Any:
        """Return the configured client or raise the shared configuration error."""
        if self._client is None:
            raise OAuthRepositoryNotConfiguredError(
                "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY."
            )
        return self._client

    @classmethod
    def _should_retry_gateway_error(cls, exc: PostgRESTAPIError, attempt: int) -> bool:
        """Return whether a gateway failure warrants another complete grant upsert."""
        code = str(exc.code) if exc.code is not None else None
        return code in cls._GATEWAY_RETRYABLE_CODES and attempt < cls._GATEWAY_RETRY_ATTEMPTS - 1

    @classmethod
    def _should_retry_device_code_insert(cls, exc: PostgRESTAPIError, attempt: int) -> bool:
        """Return whether an insert hit the pending user-code unique index and may retry."""
        code = str(exc.code) if exc.code is not None else None
        return code == cls._UNIQUE_VIOLATION_CODE and attempt < cls._DEVICE_CODE_INSERT_ATTEMPTS - 1

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

    def _device_code_payload(
        self,
        *,
        raw_device_code: str,
        client_id: str,
        scopes: list[str],
        expires_at: datetime,
        interval_seconds: int,
    ) -> dict[str, JSON]:
        """Hash a raw device code and build a pending row with a freshly issued user code."""
        return {
            "id": str(uuid4()),
            "device_code_hash": self._hash_secret(raw_device_code),
            "user_code": self._issue_user_code(),
            "client_id": client_id,
            "scopes": scopes,
            "status": "pending",
            "user_id": None,
            "grant_id": None,
            "interval_seconds": interval_seconds,
            "expires_at": expires_at.isoformat(),
            "last_polled_at": None,
            "approved_at": None,
            "created_at": datetime.now(UTC).isoformat(),
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
    def _require_device_code_row(rows: Sequence[object]) -> object:
        """Return the first written device-code row, rejecting an empty response."""
        if not rows:
            raise RuntimeError("Supabase did not return the OAuth device code row.")
        return rows[0]

    @staticmethod
    def _issue_device_code() -> str:
        """Issue a 256-bit random RFC 8628 device code."""
        return secrets.token_urlsafe(32)

    @staticmethod
    def _issue_user_code() -> str:
        """Issue a normalised (undashed) RFC 8628 user code."""
        return "".join(
            secrets.choice(DEVICE_USER_CODE_ALPHABET) for _ in range(DEVICE_USER_CODE_LENGTH)
        )

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

    @staticmethod
    def _parse_device_code(row: object) -> OAuthDeviceCodeRecord:
        """Validate an untyped PostgREST row as a device authorization."""
        if not isinstance(row, dict):
            raise TypeError("Supabase OAuth device code rows must be objects.")
        return OAuthDeviceCodeRecord.model_validate(row)

    @classmethod
    def _parse_optional_device_code(cls, rows: Sequence[object]) -> OAuthDeviceCodeRecord | None:
        """Return the first device-code row, or ``None`` when a filter matched nothing."""
        if not rows:
            return None
        return cls._parse_device_code(rows[0])
