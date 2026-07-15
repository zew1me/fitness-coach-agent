from __future__ import annotations

import base64
import hashlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, Protocol, cast
from urllib.parse import quote, urlencode

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
from backend.models.training import Activity
from backend.repos.intervals_repo import IntervalsRepository

logger = logging.getLogger(__name__)

INTERVALS_AUTHORIZE_URL = "https://intervals.icu/oauth/authorize"
INTERVALS_TOKEN_URL = "https://intervals.icu/api/oauth/token"
INTERVALS_API_BASE = "https://intervals.icu/api/v1"
INTERVALS_DEFAULT_SCOPE = "ACTIVITY:READ,WELLNESS:READ,CALENDAR:READ"
INTERVALS_STATE_TYPE = "intervals_oauth_state"
_ISO_DATE_LENGTH = 10

_INTERVALS_SPORT_MAP = {
    "ride": "cycling",
    "virtualride": "cycling",
    "ebikeride": "cycling",
    "emountainbikeride": "cycling",
    "mountainbikeride": "cycling",
    "gravelride": "cycling",
    "handcycle": "cycling",
    "velomobile": "cycling",
    "run": "running",
    "virtualrun": "running",
    "trailrun": "running",
    "swim": "swimming",
    "rowing": "rowing",
    "hike": "hiking",
    "walk": "walking",
    "snowshoe": "walking",
    "weighttraining": "strength",
    "crossfit": "strength",
    "yoga": "yoga",
}


class IntervalsConfigurationError(RuntimeError):
    """Raised when the Intervals integration cannot run because config is missing."""


class IntervalsStateError(ValueError):
    """Raised when an OAuth state value is invalid, expired, or for another user."""


class IntervalsOAuthExchangeError(RuntimeError):
    """Raised when Intervals rejects or malforms the token exchange."""


class IntervalsNotConnectedError(RuntimeError):
    """Raised when activity sync is requested without an active connection."""


class IntervalsSyncError(RuntimeError):
    """Raised when Intervals activity sync cannot fetch or validate activities."""


@dataclass(frozen=True)
class IntervalsAuthContext:
    athlete_id: str
    auth_header: dict[str, str]
    mode: Literal["oauth", "dev_api_key"]


def _dev_bypass_state() -> Literal["active", "off", "half_configured"]:
    """Return the fail-closed state of the local API-key bypass."""
    if os.environ.get("VERCEL_URL"):
        return "off"

    api_key = settings.intervals_dev_api_key.strip()
    athlete_id = settings.intervals_dev_athlete_id.strip()
    if api_key and athlete_id:
        return "active"
    if api_key or athlete_id:
        return "half_configured"
    return "off"


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

    def resolve_auth(self, user_id: str) -> IntervalsAuthContext:
        bypass_state = _dev_bypass_state()
        if bypass_state == "half_configured":
            raise IntervalsConfigurationError(
                "Intervals local development credentials must both be configured."
            )
        if bypass_state == "active":
            athlete_id = settings.intervals_dev_athlete_id.strip()
            api_key = settings.intervals_dev_api_key.strip()
            encoded = base64.b64encode(f"API_KEY:{api_key}".encode()).decode()
            logger.warning(
                "using local Intervals API-key bypass athlete_id=%s",
                athlete_id,
            )
            return IntervalsAuthContext(
                athlete_id=athlete_id,
                auth_header={"Authorization": f"Basic {encoded}"},
                mode="dev_api_key",
            )

        connection = self._repository.get_active_connection(user_id)
        if connection is None:
            raise IntervalsNotConnectedError("Intervals.icu is not connected.")
        token = TokenCipher(settings.intervals_token_encryption_secret).decrypt(
            connection.access_token_ciphertext
        )
        return IntervalsAuthContext(
            athlete_id=connection.intervals_athlete_id,
            auth_header={"Authorization": f"Bearer {token}"},
            mode="oauth",
        )

    async def fetch_recent_activities(
        self,
        auth: IntervalsAuthContext,
        *,
        oldest: date,
        newest: date,
    ) -> list[dict[str, Any]]:
        athlete_id = quote(auth.athlete_id, safe="")
        url = f"{INTERVALS_API_BASE}/athlete/{athlete_id}/activities"
        async with self._http_client_factory() as client:
            try:
                response = await client.get(
                    url,
                    params={"oldest": oldest.isoformat(), "newest": newest.isoformat()},
                    headers=auth.auth_header,
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise IntervalsSyncError("Intervals.icu activities could not be fetched.") from exc

        try:
            payload: object = response.json()
        except ValueError as exc:
            raise IntervalsSyncError(
                "Intervals.icu returned an invalid activities response."
            ) from exc
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise IntervalsSyncError("Intervals.icu returned an invalid activities response.")
        return [cast(dict[str, Any], item) for item in payload]

    def disconnect(self, user_id: str) -> IntervalsConnectionStatus:
        self._repository.revoke_active_connection(user_id)
        return IntervalsConnectionStatus(connected=False)

    async def _exchange_code(self, code: str) -> IntervalsTokenResponse:
        async with self._http_client_factory() as client:
            try:
                response = await client.post(
                    INTERVALS_TOKEN_URL,
                    json={
                        "grant_type": "authorization_code",
                        "code": code,
                        "client_id": settings.intervals_client_id,
                        "client_secret": settings.intervals_client_secret,
                        "redirect_uri": self._redirect_uri(),
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


def map_intervals_activity(user_id: str, item: dict[str, Any]) -> Activity | None:
    raw_intervals_id = item.get("id")
    if isinstance(raw_intervals_id, bool) or not isinstance(raw_intervals_id, str | int):
        return None
    intervals_id = str(raw_intervals_id).strip()
    activity_date, started_at = _parse_activity_dates(item)
    if not intervals_id or activity_date is None:
        return None

    duration_seconds = _first_positive_int(item.get("moving_time"), item.get("elapsed_time"))
    # Intervals reports intensity as a percentage (for example 86.0 means IF 0.86).
    intensity = _optional_float(item.get("icu_intensity"))
    try:
        return Activity(
            user_id=user_id,
            sport=_map_intervals_sport(item.get("type")),
            activity_date=activity_date,
            started_at=started_at,
            duration_seconds=duration_seconds,
            distance_meters=_optional_float(item.get("distance")),
            elevation_gain_meters=_optional_float(item.get("total_elevation_gain")),
            avg_hr_bpm=_optional_int(item.get("average_heartrate")),
            max_hr_bpm=_optional_int(item.get("max_heartrate")),
            avg_power_watts=_optional_int(item.get("average_watts")),
            normalized_power_watts=_optional_int(item.get("icu_weighted_avg_watts")),
            avg_cadence_rpm=_optional_int(item.get("average_cadence")),
            tss=_optional_float(item.get("icu_training_load")),
            intensity_factor=(intensity / 100 if intensity is not None else None),
            rpe=_optional_int(item.get("perceived_exertion")),
            source="intervals_sync",
            source_file_key=f"intervals:{intervals_id}",
            raw_extraction={"intervals_summary": item},
        )
    except (TypeError, ValueError):
        logger.warning("skipping malformed Intervals activity id=%s", intervals_id)
        return None


def _map_intervals_sport(value: object) -> str:
    normalized = str(value or "").strip().casefold().replace("_", "").replace(" ", "")
    return _INTERVALS_SPORT_MAP.get(normalized, "general")


def _parse_activity_dates(item: dict[str, Any]) -> tuple[date | None, datetime | None]:
    local_value = item.get("start_date_local")
    absolute_value = item.get("start_date")
    activity_date = _optional_date(local_value) or _optional_date(absolute_value)
    started_at = _optional_datetime(absolute_value) or _optional_datetime(local_value)
    return activity_date, started_at


def _optional_date(value: object) -> date | None:
    if not isinstance(value, str) or len(value) < _ISO_DATE_LENGTH:
        return None
    try:
        return date.fromisoformat(value[:_ISO_DATE_LENGTH])
    except ValueError:
        return None


def _optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    number = _optional_float(value)
    return round(number) if number is not None else None


def _first_positive_int(*values: object) -> int | None:
    for value in values:
        number = _optional_int(value)
        if number is not None and number > 0:
            return number
    return None
