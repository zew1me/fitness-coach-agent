from __future__ import annotations

import base64
import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken
from pydantic import ValidationError

from backend.config import settings
from backend.models.intervals import (
    IntervalsAuthorizationResponse,
    IntervalsConnectionCreate,
    IntervalsConnectionRecord,
    IntervalsConnectionStatus,
    IntervalsOAuthState,
    IntervalsTokenResponse,
)
from backend.repos.intervals_repo import IntervalsRepository

logger = logging.getLogger(__name__)

INTERVALS_AUTHORIZE_URL = "https://intervals.icu/oauth/authorize"
INTERVALS_TOKEN_URL = "https://intervals.icu/api/oauth/token"
INTERVALS_DEFAULT_SCOPE = "ACTIVITY:READ,WELLNESS:READ,CALENDAR:READ"
INTERVALS_STATE_TYPE = "intervals_oauth_state"


class IntervalsConfigurationError(RuntimeError):
    """Raised when the Intervals integration cannot run because config is missing."""


class IntervalsStateError(ValueError):
    """Raised when an OAuth state value is invalid, expired, or for another user."""


class IntervalsOAuthExchangeError(RuntimeError):
    """Raised when Intervals rejects or malforms the token exchange."""


class TokenCipher:
    """Encrypt Intervals bearer tokens before persistence."""

    def __init__(self, secret: str) -> None:
        if not secret.strip():
            raise IntervalsConfigurationError("Intervals.icu integration is not configured yet.")
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        self._fernet = Fernet(key)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise IntervalsOAuthExchangeError(
                "Stored Intervals token could not be decrypted."
            ) from exc


class IntervalsConnectionRepository(Protocol):
    def get_active_connection(self, user_id: str) -> IntervalsConnectionRecord | None: ...

    def replace_connection(
        self, connection: IntervalsConnectionCreate
    ) -> IntervalsConnectionRecord: ...

    def revoke_active_connection(self, user_id: str) -> bool: ...


class IntervalsOAuthService:
    """Owns Intervals.icu OAuth URL construction, token exchange, and status."""

    def __init__(
        self,
        repository: IntervalsConnectionRepository | None = None,
        *,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._repository = repository or IntervalsRepository()
        self._http_client_factory = http_client_factory or (lambda: httpx.AsyncClient(timeout=10.0))

    def build_authorization_url(self, user_id: str) -> IntervalsAuthorizationResponse:
        client_id = self._require_client_id()
        state = self.create_state(user_id=user_id)
        redirect_uri = self._redirect_uri()
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": INTERVALS_DEFAULT_SCOPE,
                "state": state,
            }
        )
        return IntervalsAuthorizationResponse(redirect_url=f"{INTERVALS_AUTHORIZE_URL}?{query}")

    def create_state(self, *, user_id: str, ttl_seconds: int = 10 * 60) -> str:
        now = datetime.now(UTC)
        return jwt.encode(
            {
                "typ": INTERVALS_STATE_TYPE,
                "sub": user_id,
                "iat": now,
                "exp": now + timedelta(seconds=ttl_seconds),
            },
            settings.app_jwt_secret,
            algorithm="HS256",
        )

    def validate_state(
        self, state: str, *, expected_user_id: str | None = None
    ) -> IntervalsOAuthState:
        try:
            claims = jwt.decode(state, settings.app_jwt_secret, algorithms=["HS256"])
        except jwt.PyJWTError as exc:
            raise IntervalsStateError("Invalid Intervals authorization state.") from exc

        if claims.get("typ") != INTERVALS_STATE_TYPE:
            raise IntervalsStateError("Invalid Intervals authorization state.")
        user_id = str(claims.get("sub", ""))
        if not user_id:
            raise IntervalsStateError("Invalid Intervals authorization state.")
        if expected_user_id is not None and user_id != expected_user_id:
            raise IntervalsStateError("Intervals authorization state does not match this user.")
        return IntervalsOAuthState(user_id=user_id)

    async def exchange_code_for_connection(
        self, *, code: str, state: str
    ) -> IntervalsConnectionStatus:
        state_context = self.validate_state(state)
        self._require_exchange_config()
        token = await self._exchange_code(code)
        if token.token_type.lower() != "bearer":
            raise IntervalsOAuthExchangeError("Intervals returned an unsupported token type.")
        cipher = TokenCipher(settings.intervals_token_encryption_secret)
        connection = self._repository.replace_connection(
            IntervalsConnectionCreate(
                user_id=state_context.user_id,
                intervals_athlete_id=token.athlete.id,
                intervals_athlete_name=token.athlete.name,
                scopes=token.scopes,
                access_token_ciphertext=cipher.encrypt(token.access_token),
                token_type=token.token_type,
            )
        )
        logger.info("intervals connection stored scopes=%s", token.scopes)
        return self._status_from_record(connection)

    def get_status(self, user_id: str) -> IntervalsConnectionStatus:
        return self._status_from_record(self._repository.get_active_connection(user_id))

    def disconnect(self, user_id: str) -> IntervalsConnectionStatus:
        self._repository.revoke_active_connection(user_id)
        return IntervalsConnectionStatus(connected=False)

    async def _exchange_code(self, code: str) -> IntervalsTokenResponse:
        async with self._http_client_factory() as client:
            try:
                response = await client.post(
                    INTERVALS_TOKEN_URL,
                    data={
                        "client_id": settings.intervals_client_id,
                        "client_secret": settings.intervals_client_secret,
                        "code": code,
                    },
                )
                response.raise_for_status()
                return IntervalsTokenResponse.model_validate(response.json())
            except (httpx.HTTPError, ValidationError) as exc:
                raise IntervalsOAuthExchangeError(
                    "Intervals.icu authorization could not be completed."
                ) from exc

    def _redirect_uri(self) -> str:
        return f"{settings.base_url.rstrip('/')}/api/intervals/callback"

    @staticmethod
    def _status_from_record(
        record: IntervalsConnectionRecord | None,
    ) -> IntervalsConnectionStatus:
        if record is None:
            return IntervalsConnectionStatus(connected=False)
        return IntervalsConnectionStatus(
            connected=True,
            connected_at=record.connected_at,
            intervals_athlete_id=record.intervals_athlete_id,
            intervals_athlete_name=record.intervals_athlete_name,
            scopes=record.scopes,
        )

    @staticmethod
    def _require_client_id() -> str:
        client_id = settings.intervals_client_id.strip()
        if not client_id:
            raise IntervalsConfigurationError("Intervals.icu integration is not configured yet.")
        return client_id

    @staticmethod
    def _require_exchange_config() -> None:
        if (
            not settings.intervals_client_id.strip()
            or not settings.intervals_client_secret.strip()
            or not settings.intervals_token_encryption_secret.strip()
        ):
            raise IntervalsConfigurationError("Intervals.icu integration is not configured yet.")
