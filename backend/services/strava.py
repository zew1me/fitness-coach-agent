"""Async Strava OAuth and activity-sync boundary.

This module intentionally uses the application's existing ``httpx`` transport
instead of ``stravalib``.  The library is synchronous and its rate limiter
sleeps in the request thread, while these FastAPI handlers require non-blocking
I/O and application-owned token rotation/persistence.  Keep that decision in
sync with ``docs/strava-integration-runbook.md``.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from statistics import median
from typing import Any, Protocol, cast
from urllib.parse import urlencode

import httpx
import jwt
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from backend.config import settings
from backend.engine.tss import compute_normalized_power, compute_tss
from backend.engine.zones import Zone, compute_zones
from backend.models.athlete import AthleteProfile, SportThreshold
from backend.models.strava import (
    STRAVA_REFRESH_THRESHOLD_SECONDS,
    StravaAuthorizationResponse,
    StravaConnectionCreate,
    StravaConnectionRecord,
    StravaConnectionStatus,
    StravaOAuthState,
    StravaRefreshResponse,
    StravaTokenResponse,
    StravaTokenRotation,
    has_required_activity_scope,
    normalize_strava_scopes,
)
from backend.models.training import Activity
from backend.repos.strava_repo import StravaRepository
from backend.services.activity_parse import (
    first_positive_int as _first_positive_int,
)
from backend.services.activity_parse import (
    optional_date as _optional_date,
)
from backend.services.activity_parse import (
    optional_datetime as _optional_datetime,
)
from backend.services.activity_parse import (
    optional_float as _optional_float,
)
from backend.services.activity_parse import (
    optional_int as _optional_int,
)
from backend.services.intervals import TokenCipher

logger = logging.getLogger(__name__)

STRAVA_AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_REVOKE_URL = "https://www.strava.com/oauth/revoke"
STRAVA_API_BASE = "https://www.strava.com/api/v3"
# Full read access is explicitly requested so the athlete can import their own
# Only Me activities and their processed non-GPS streams.
STRAVA_DEFAULT_SCOPE = "read,activity:read_all"
STRAVA_STATE_TYPE = "strava_oauth_state"

# Bound a manual sync so a single request can never walk the athlete's entire
# history and exhaust the rate-limit budget.
STRAVA_SYNC_MAX_DAYS = 90
STRAVA_SYNC_PER_PAGE = 100
STRAVA_SYNC_MAX_PAGES = 10
# Preserve headroom for the paginated summary request and follow-up actions under
# Strava's short-window read limit. Remaining activities use summary fallbacks.
STRAVA_SYNC_MAX_STREAM_REQUESTS = 75
STRAVA_STREAM_MAX_SAMPLES = 200_000
STRAVA_STREAM_KEYS = (
    "time",
    "heartrate",
    "cadence",
    "watts",
    "velocity_smooth",
    "moving",
)

StravaStreamValue = int | float | bool
StravaStreams = dict[str, list[StravaStreamValue]]
_SENSOR_STREAM_BOUNDS = {
    "watts": (0.0, 5_000.0),
    "heartrate": (20.0, 250.0),
    "cadence": (0.0, 250.0),
    "velocity_smooth": (0.0, 50.0),
}

# Strava sport_type → canonical sport. Keys are normalized (casefold, no spaces
# or underscores). Prefer sport_type over the deprecated `type` field.
_STRAVA_SPORT_MAP = {
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
    "virtualrow": "rowing",
    "kayaking": "rowing",
    "canoeing": "rowing",
    "standuppaddling": "rowing",
    "hike": "hiking",
    "snowshoe": "hiking",
    "walk": "walking",
    "wheelchair": "walking",
    "weighttraining": "strength",
    "crossfit": "strength",
    "highintensityintervaltraining": "strength",
    "stairstepper": "strength",
    "elliptical": "strength",
    "workout": "general",
    "yoga": "yoga",
    "pilates": "yoga",
}

# Summary fields we retain for provenance. Deliberately excludes map/polyline,
# GPS coordinates, photos, segment efforts, social counts, and upload ids.
_STRAVA_PROVENANCE_FIELDS = (
    "id",
    "sport_type",
    "type",
    "start_date",
    "start_date_local",
    "moving_time",
    "elapsed_time",
    "distance",
    "total_elevation_gain",
    "average_heartrate",
    "max_heartrate",
    "average_watts",
    "weighted_average_watts",
    "average_cadence",
    "name",
    "device_name",
)


class StravaConfigurationError(RuntimeError):
    """Raised when the Strava integration is disabled or missing configuration."""


class StravaStateError(ValueError):
    """Raised when an OAuth state value is invalid, expired, or for another user."""


class StravaOAuthExchangeError(RuntimeError):
    """Raised when Strava rejects or malforms the token exchange."""


class StravaScopeError(RuntimeError):
    """Raised when the athlete did not grant the required activity read scope."""


class StravaNotConnectedError(RuntimeError):
    """Raised when an operation is requested without an active connection."""


class StravaReconnectRequiredError(RuntimeError):
    """Raised when Strava rejects the stored refresh token (invalid_grant/401)."""


class StravaSyncError(RuntimeError):
    """Raised when Strava activity sync cannot fetch or validate activities."""


class StravaRateLimitError(RuntimeError):
    """Raised when Strava returns 429; carries bounded retry guidance."""

    def __init__(self, message: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class StravaAuthContext:
    connection: StravaConnectionRecord
    access_token: str


@dataclass(frozen=True)
class StravaDisconnectResult:
    status: StravaConnectionStatus
    remote_revoked: bool


class StravaConnectionRepository(Protocol):
    """Structural type documenting the repository surface the service needs."""

    def get_active_connection(self, user_id: str) -> StravaConnectionRecord | None: ...

    def replace_connection(self, connection: StravaConnectionCreate) -> StravaConnectionRecord: ...

    def rotate_tokens(
        self, *, connection_id: str, expected_expires_at: datetime, rotation: StravaTokenRotation
    ) -> StravaConnectionRecord | None: ...

    def touch_last_sync(self, user_id: str) -> None: ...

    def revoke_active_connection(self, user_id: str) -> bool: ...


class StravaOAuthService:
    """Owns Strava OAuth URL construction, rotating-token refresh, sync, and revocation."""

    def __init__(
        self,
        repository: StravaConnectionRepository | None = None,
        *,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._repository = repository or cast(StravaConnectionRepository, StravaRepository())
        self._http_client_factory = http_client_factory or (lambda: httpx.AsyncClient(timeout=10.0))

    # ── Authorization ────────────────────────────────────────────

    def build_authorization_url(self, user_id: str) -> StravaAuthorizationResponse:
        self._require_enabled()
        state = self.create_state(user_id=user_id)
        query = urlencode(
            {
                "client_id": self._client_id(),
                "redirect_uri": self._redirect_uri(),
                "response_type": "code",
                "approval_prompt": "auto",
                "scope": STRAVA_DEFAULT_SCOPE,
                "state": state,
            }
        )
        return StravaAuthorizationResponse(redirect_url=f"{STRAVA_AUTHORIZE_URL}?{query}")

    def create_state(self, *, user_id: str, ttl_seconds: int = 10 * 60) -> str:
        now = datetime.now(UTC)
        return jwt.encode(
            {
                "typ": STRAVA_STATE_TYPE,
                "sub": user_id,
                "iat": now,
                "exp": now + timedelta(seconds=ttl_seconds),
            },
            settings.app_jwt_secret,
            algorithm="HS256",
        )

    def validate_state(self, state: str) -> StravaOAuthState:
        try:
            claims = jwt.decode(state, settings.app_jwt_secret, algorithms=["HS256"])
        except jwt.PyJWTError as exc:
            raise StravaStateError("Invalid Strava authorization state.") from exc
        if claims.get("typ") != STRAVA_STATE_TYPE:
            raise StravaStateError("Invalid Strava authorization state.")
        user_id = str(claims.get("sub", ""))
        if not user_id:
            raise StravaStateError("Invalid Strava authorization state.")
        return StravaOAuthState(user_id=user_id)

    # ── Callback / exchange ──────────────────────────────────────

    async def exchange_code_for_connection(
        self, *, code: str, scope: str | None, state: str
    ) -> StravaConnectionStatus:
        self._require_enabled()
        state_context = self.validate_state(state)
        token = await self._exchange_code(code)
        # Strava reports the granted scope on the callback query, not the token
        # body; fall back to the default only if the callback omitted it.
        scopes = normalize_strava_scopes(scope or STRAVA_DEFAULT_SCOPE)
        if not has_required_activity_scope(scopes):
            raise StravaScopeError("Strava activity read scope was not granted.")
        cipher = self._cipher()
        connection = await run_in_threadpool(
            self._repository.replace_connection,
            StravaConnectionCreate(
                user_id=state_context.user_id,
                strava_athlete_id=token.athlete.id,
                strava_athlete_name=token.athlete.display_name,
                scopes=scopes,
                access_token_ciphertext=cipher.encrypt(token.access_token),
                refresh_token_ciphertext=cipher.encrypt(token.refresh_token),
                token_type=token.token_type,
                expires_at=token.expires_at_datetime,
                authorization_version=settings.strava_authorization_version or None,
            ),
        )
        logger.info("strava connection stored scopes=%s", scopes)
        return self._status_from_record(connection)

    # ── Status / auth resolution ─────────────────────────────────

    async def get_status(self, user_id: str) -> StravaConnectionStatus:
        # Status is a read-only probe: when the integration is disabled or
        # unconfigured, report "not connected" rather than raising, so the
        # profile renders a clean Connect affordance instead of an error banner
        # (mirrors Intervals' unconfigured behavior). Action endpoints still 503.
        if not self._is_enabled():
            return StravaConnectionStatus(connected=False)
        connection = await run_in_threadpool(self._repository.get_active_connection, user_id)
        return self._status_from_record(connection)

    async def record_sync(self, user_id: str) -> None:
        """Best-effort stamp of the last successful sync on the active connection."""
        await run_in_threadpool(self._repository.touch_last_sync, user_id)

    async def resolve_auth(self, user_id: str) -> StravaAuthContext:
        self._require_enabled()
        connection = await run_in_threadpool(self._repository.get_active_connection, user_id)
        if connection is None:
            raise StravaNotConnectedError("Strava is not connected.")
        if not has_required_activity_scope(connection.scopes):
            raise StravaReconnectRequiredError(
                "Reconnect Strava and grant access to all of your activities."
            )
        access_token, connection = await self._ensure_fresh_token(connection)
        return StravaAuthContext(connection=connection, access_token=access_token)

    async def _ensure_fresh_token(
        self, connection: StravaConnectionRecord
    ) -> tuple[str, StravaConnectionRecord]:
        cipher = self._cipher()
        threshold = datetime.now(UTC) + timedelta(seconds=STRAVA_REFRESH_THRESHOLD_SECONDS)
        if connection.expires_at > threshold:
            return cipher.decrypt(connection.access_token_ciphertext), connection

        refresh_token = cipher.decrypt(connection.refresh_token_ciphertext)
        refreshed = await self._refresh_tokens(refresh_token)
        rotation = StravaTokenRotation(
            access_token_ciphertext=cipher.encrypt(refreshed.access_token),
            refresh_token_ciphertext=cipher.encrypt(refreshed.refresh_token),
            token_type=refreshed.token_type,
            expires_at=refreshed.expires_at_datetime,
        )
        rotated = await run_in_threadpool(
            self._repository.rotate_tokens,
            connection_id=connection.id,
            expected_expires_at=connection.expires_at,
            rotation=rotation,
        )
        if rotated is not None:
            return refreshed.access_token, rotated

        # A concurrent refresh already rotated the token; reload and use theirs so
        # we never race a stale-token write against a newer one.
        reloaded = await run_in_threadpool(
            self._repository.get_active_connection, connection.user_id
        )
        if reloaded is None:
            raise StravaReconnectRequiredError("Strava connection is no longer active.")
        return cipher.decrypt(reloaded.access_token_ciphertext), reloaded

    # ── Activity fetch ───────────────────────────────────────────

    async def fetch_activities(
        self, auth: StravaAuthContext, *, after: datetime, before: datetime
    ) -> list[dict[str, Any]]:
        headers = {"Authorization": f"Bearer {auth.access_token}"}
        collected: list[dict[str, Any]] = []
        async with self._http_client_factory() as client:
            for page in range(1, STRAVA_SYNC_MAX_PAGES + 1):
                page_items = await self._fetch_activity_page(
                    client, headers, after=after, before=before, page=page
                )
                collected.extend(page_items)
                if len(page_items) < STRAVA_SYNC_PER_PAGE:
                    break
        return collected

    async def _fetch_activity_page(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        *,
        after: datetime,
        before: datetime,
        page: int,
    ) -> list[dict[str, Any]]:
        try:
            response = await client.get(
                f"{STRAVA_API_BASE}/athlete/activities",
                params={
                    "after": int(after.timestamp()),
                    "before": int(before.timestamp()),
                    "page": page,
                    "per_page": STRAVA_SYNC_PER_PAGE,
                },
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise StravaSyncError("Strava activities could not be fetched.") from exc

        self._log_rate_limit(response)
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            raise StravaRateLimitError(
                "Strava rate limit reached. Try again after the next reset.",
                retry_after_seconds=_seconds_to_next_quarter_hour(),
            )
        if response.status_code == httpx.codes.UNAUTHORIZED:
            raise StravaReconnectRequiredError("Strava rejected the access token.")
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise StravaSyncError("Strava activities could not be fetched.") from exc

        try:
            payload: object = response.json()
        except ValueError as exc:
            raise StravaSyncError("Strava returned an invalid activities response.") from exc
        if not isinstance(payload, list):
            raise StravaSyncError("Strava returned an invalid activities response.")
        return [item for item in payload if isinstance(item, dict)]

    async def fetch_activity_streams(
        self,
        auth: StravaAuthContext,
        activity_id: str,
    ) -> StravaStreams:
        """Fetch processed non-GPS streams; unavailable streams degrade to summaries."""
        headers = {"Authorization": f"Bearer {auth.access_token}"}
        try:
            async with self._http_client_factory() as client:
                response = await client.get(
                    f"{STRAVA_API_BASE}/activities/{activity_id}/streams",
                    params={
                        "keys": ",".join(STRAVA_STREAM_KEYS),
                        "key_by_type": "true",
                    },
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise StravaSyncError("Strava activity streams could not be fetched.") from exc

        self._log_rate_limit(response)
        if response.status_code == httpx.codes.NOT_FOUND:
            return {}
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            raise StravaRateLimitError(
                "Strava rate limit reached. Try again after the next reset.",
                retry_after_seconds=_seconds_to_next_quarter_hour(),
            )
        if response.status_code == httpx.codes.UNAUTHORIZED:
            raise StravaReconnectRequiredError("Strava rejected the access token.")
        if response.status_code == httpx.codes.FORBIDDEN:
            raise StravaReconnectRequiredError(
                "Reconnect Strava and grant access to all of your activities."
            )
        try:
            response.raise_for_status()
            payload: object = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise StravaSyncError("Strava returned invalid activity streams.") from exc
        return _parse_streams(payload)

    # ── Disconnect / revocation ──────────────────────────────────

    async def disconnect(self, user_id: str) -> StravaDisconnectResult:
        self._require_enabled()
        connection = await run_in_threadpool(self._repository.get_active_connection, user_id)
        if connection is None:
            return StravaDisconnectResult(
                status=StravaConnectionStatus(connected=False), remote_revoked=True
            )

        access_token = self._cipher().decrypt(connection.access_token_ciphertext)
        try:
            await self._revoke(access_token)
        except StravaSyncError:
            # Retryable upstream failure: keep credentials so a retry can revoke,
            # but block reads by leaving the connection marked pending.
            logger.warning("strava remote revocation deferred user_id=%s", user_id)
            return StravaDisconnectResult(
                status=self._status_from_record(connection, disconnect_pending=True),
                remote_revoked=False,
            )

        await run_in_threadpool(self._repository.revoke_active_connection, user_id)
        return StravaDisconnectResult(
            status=StravaConnectionStatus(connected=False), remote_revoked=True
        )

    # ── HTTP helpers ─────────────────────────────────────────────

    async def _exchange_code(self, code: str) -> StravaTokenResponse:
        async with self._http_client_factory() as client:
            try:
                response = await client.post(
                    STRAVA_TOKEN_URL,
                    data={
                        "client_id": self._client_id(),
                        "client_secret": settings.strava_client_secret,
                        "code": code,
                        "grant_type": "authorization_code",
                    },
                )
                response.raise_for_status()
                return StravaTokenResponse.model_validate(response.json())
            except (httpx.HTTPError, ValidationError, ValueError) as exc:
                raise StravaOAuthExchangeError(
                    "Strava authorization could not be completed."
                ) from exc

    async def _refresh_tokens(self, refresh_token: str) -> StravaRefreshResponse:
        async with self._http_client_factory() as client:
            try:
                response = await client.post(
                    STRAVA_TOKEN_URL,
                    data={
                        "client_id": self._client_id(),
                        "client_secret": settings.strava_client_secret,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    },
                )
            except httpx.HTTPError as exc:
                raise StravaSyncError("Strava token refresh failed.") from exc
            if response.status_code in (
                httpx.codes.BAD_REQUEST,
                httpx.codes.UNAUTHORIZED,
            ):
                # invalid_grant — the stored refresh token is dead; do not retry.
                raise StravaReconnectRequiredError("Strava refresh token was rejected.")
            try:
                response.raise_for_status()
                return StravaRefreshResponse.model_validate(response.json())
            except (httpx.HTTPError, ValidationError, ValueError) as exc:
                raise StravaSyncError("Strava token refresh failed.") from exc

    async def _revoke(self, access_token: str) -> None:
        async with self._http_client_factory() as client:
            try:
                response = await client.post(
                    STRAVA_REVOKE_URL,
                    auth=httpx.BasicAuth(self._client_id(), settings.strava_client_secret),
                    data={"token": access_token},
                )
            except httpx.HTTPError as exc:
                raise StravaSyncError("Strava revocation failed.") from exc
            if response.status_code == httpx.codes.OK:
                return
            raise StravaSyncError("Strava revocation failed.")

    # ── Rate-limit observability ─────────────────────────────────

    @staticmethod
    def _log_rate_limit(response: httpx.Response) -> None:
        usage = response.headers.get("X-RateLimit-Usage")
        limit = response.headers.get("X-RateLimit-Limit")
        read_usage = response.headers.get("X-ReadRateLimit-Usage")
        if usage or read_usage:
            logger.info(
                "strava rate limit usage=%s limit=%s read_usage=%s",
                usage,
                limit,
                read_usage,
            )

    # ── Config / cipher ──────────────────────────────────────────

    @staticmethod
    def _is_enabled() -> bool:
        return (
            settings.strava_integration_enabled
            and bool(settings.strava_client_id.strip())
            and bool(settings.strava_client_secret.strip())
            and bool(settings.strava_token_encryption_secret.strip())
        )

    def _require_enabled(self) -> None:
        if not settings.strava_integration_enabled:
            raise StravaConfigurationError("Strava integration is not enabled.")
        if (
            not settings.strava_client_id.strip()
            or not settings.strava_client_secret.strip()
            or not settings.strava_token_encryption_secret.strip()
        ):
            raise StravaConfigurationError("Strava integration is not configured yet.")

    @staticmethod
    def _client_id() -> str:
        return settings.strava_client_id.strip()

    @staticmethod
    def _cipher() -> TokenCipher:
        return TokenCipher(settings.strava_token_encryption_secret)

    def _redirect_uri(self) -> str:
        return f"{settings.base_url.rstrip('/')}/api/strava/callback"

    @staticmethod
    def _status_from_record(
        record: StravaConnectionRecord | None, *, disconnect_pending: bool = False
    ) -> StravaConnectionStatus:
        if record is None:
            return StravaConnectionStatus(connected=False)
        return StravaConnectionStatus(
            connected=True,
            disconnect_pending=disconnect_pending,
            connected_at=record.connected_at,
            last_sync_at=record.last_sync_at,
            strava_athlete_id=record.strava_athlete_id,
            strava_athlete_name=record.strava_athlete_name,
            scopes=record.scopes,
            authorization_version=record.authorization_version,
        )


def _seconds_to_next_quarter_hour(now: datetime | None = None) -> int:
    """Seconds until Strava's next 15-minute rate-limit reset (UTC)."""
    current = now or datetime.now(UTC)
    minutes_into_block = current.minute % 15
    reset = current.replace(second=0, microsecond=0) + timedelta(minutes=15 - minutes_into_block)
    return max(1, int((reset - current).total_seconds()))


# ── Activity mapping ─────────────────────────────────────────────


def _parse_streams(payload: object) -> StravaStreams:
    if not isinstance(payload, dict):
        return {}
    payload_by_key = cast(dict[str, object], payload)
    streams: StravaStreams = {}
    for key in STRAVA_STREAM_KEYS:
        stream = payload_by_key.get(key)
        if not isinstance(stream, dict):
            continue
        stream_by_key = cast(dict[str, object], stream)
        data = stream_by_key.get("data")
        if not isinstance(data, list) or len(data) > STRAVA_STREAM_MAX_SAMPLES:
            continue
        values: list[StravaStreamValue] = []
        for value in cast(list[object], data):
            is_moving_value = key == "moving" and isinstance(value, bool)
            is_finite_number = (
                key != "moving"
                and not isinstance(value, bool)
                and isinstance(value, int | float)
                and math.isfinite(value)
            )
            if is_moving_value or is_finite_number:
                values.append(cast(StravaStreamValue, value))
        if values:
            streams[key] = values
    return streams


def _numeric_stream(streams: StravaStreams, key: str) -> list[float]:
    return [
        float(value)
        for value in streams.get(key, [])
        if not isinstance(value, bool) and isinstance(value, int | float)
    ]


def _usable_sensor_stream(streams: StravaStreams, key: str) -> list[float]:
    values = _numeric_stream(streams, key)
    bounds = _SENSOR_STREAM_BOUNDS[key]
    shaped = [min(max(value, bounds[0]), bounds[1]) for value in values]
    return shaped if any(value > 0 for value in shaped) else []


def _moving_values(values: list[float], streams: StravaStreams) -> list[float]:
    moving = streams.get("moving", [])
    return [
        value
        for index, value in enumerate(values)
        if index >= len(moving) or moving[index] is not False
    ]


def _sample_weights(times: list[float], count: int) -> list[float]:
    deltas = [times[index + 1] - times[index] for index in range(min(len(times) - 1, count - 1))]
    positive = [delta for delta in deltas if delta > 0]
    fallback = float(median(positive)) if positive else 1.0
    return [
        deltas[index] if index < len(deltas) and deltas[index] > 0 else fallback
        for index in range(count)
    ]


def _zone_name(
    value: float,
    zones: list[Zone],
    *,
    low_field: str,
    high_field: str,
) -> str | None:
    candidates: list[tuple[float, str]] = []
    for zone in zones:
        low = getattr(zone, low_field)
        high = getattr(zone, high_field)
        if low is None or high is None:
            continue
        lower, upper = sorted((low, high))
        name = f"zone_{zone.number}"
        if lower <= value <= upper:
            return name
        candidates.append((min(abs(value - lower), abs(value - upper)), name))
    return min(candidates)[1] if candidates else None


def _zone_distribution(
    values: list[float],
    streams: StravaStreams,
    zones: list[Zone],
    *,
    low_field: str,
    high_field: str,
) -> dict[str, float] | None:
    if not values or not zones:
        return None
    times = _numeric_stream(streams, "time")
    weights = _sample_weights(times, len(values))
    moving = streams.get("moving", [])
    totals = {f"zone_{zone.number}": 0.0 for zone in zones}
    total_weight = 0.0
    for index, value in enumerate(values):
        if index < len(moving) and moving[index] is False:
            continue
        selected = _zone_name(
            value,
            zones,
            low_field=low_field,
            high_field=high_field,
        )
        if selected is not None:
            totals[selected] += weights[index]
            total_weight += weights[index]
    if total_weight <= 0:
        return None
    return {name: round(weight / total_weight * 100, 1) for name, weight in totals.items()}


def _stream_sample_rate(streams: StravaStreams) -> int:
    times = _numeric_stream(streams, "time")
    positive = [later - earlier for earlier, later in pairwise(times) if later > earlier]
    return max(1, round(median(positive))) if positive else 1


@dataclass(frozen=True)
class _StreamObservations:
    power: list[float]
    heart_rate: list[float]
    pace: list[float]
    normalized_power: int | None
    average_power: int | None
    average_heart_rate: int | None
    average_pace: int | None
    average_cadence: int | None


@dataclass(frozen=True)
class _LoadEstimate:
    intensity_factor: float | None = None
    tss: float | None = None
    method: str | None = None


def _stream_observations(
    activity: Activity,
    streams: StravaStreams,
    duration: int,
) -> _StreamObservations:
    power = _usable_sensor_stream(streams, "watts")
    heart_rate = _usable_sensor_stream(streams, "heartrate")
    cadence = _usable_sensor_stream(streams, "cadence")
    velocity = _usable_sensor_stream(streams, "velocity_smooth")
    first_velocity = next((value for value in velocity if value > 0), None)
    pace = (
        [1000 / (value if value > 0 else first_velocity) for value in velocity]
        if first_velocity
        else []
    )
    moving_power = _moving_values(power, streams)
    moving_heart_rate = _moving_values(heart_rate, streams)
    moving_pace = _moving_values(pace, streams)
    moving_cadence = _moving_values(cadence, streams)
    normalized_power = activity.normalized_power_watts
    if moving_power:
        normalized_power = compute_normalized_power(
            [round(value) for value in moving_power],
            sample_rate_seconds=_stream_sample_rate(streams),
        )
    average_power = (
        round(sum(moving_power) / len(moving_power)) if moving_power else activity.avg_power_watts
    )
    average_heart_rate = (
        round(sum(moving_heart_rate) / len(moving_heart_rate))
        if moving_heart_rate
        else activity.avg_hr_bpm
    )
    average_pace = (
        round(sum(moving_pace) / len(moving_pace)) if moving_pace else activity.avg_pace_sec_per_km
    )
    average_cadence = (
        round(sum(moving_cadence) / len(moving_cadence))
        if moving_cadence
        else activity.avg_cadence_rpm
    )
    if (
        average_pace is None
        and activity.sport == "running"
        and activity.distance_meters is not None
        and activity.distance_meters > 0
    ):
        average_pace = round(duration / (activity.distance_meters / 1000))
    return _StreamObservations(
        power=power,
        heart_rate=heart_rate,
        pace=pace,
        normalized_power=normalized_power,
        average_power=average_power,
        average_heart_rate=average_heart_rate,
        average_pace=average_pace,
        average_cadence=average_cadence,
    )


def _stream_zone_distribution(
    activity: Activity,
    profile: AthleteProfile,
    threshold: SportThreshold | None,
    streams: StravaStreams,
    observations: _StreamObservations,
) -> dict[str, float] | None:
    ftp = threshold.lt2_power_watts if threshold else None
    if observations.power and ftp:
        zones = compute_zones(
            "cycling",
            ftp_watts=ftp,
            lt1_power_watts=threshold.lt1_power_watts if threshold else None,
        )
        return _zone_distribution(
            observations.power,
            streams,
            zones,
            low_field="power_low",
            high_field="power_high",
        )
    if activity.sport == "running" and observations.pace and threshold:
        zones = compute_zones(
            "running",
            lt2_pace_sec_km=threshold.lt2_pace_sec_per_km,
            lt1_pace_sec_km=threshold.lt1_pace_sec_per_km,
        )
        return _zone_distribution(
            observations.pace,
            streams,
            zones,
            low_field="pace_low_sec_km",
            high_field="pace_high_sec_km",
        )
    if observations.heart_rate and profile.max_hr_bpm:
        zones = compute_zones(
            "general",
            max_hr=profile.max_hr_bpm,
            lt2_hr=threshold.lt2_hr_bpm if threshold else None,
            lt1_hr=threshold.lt1_hr_bpm if threshold else None,
        )
        return _zone_distribution(
            observations.heart_rate,
            streams,
            zones,
            low_field="hr_low",
            high_field="hr_high",
        )
    return None


def _training_load_estimate(
    activity: Activity,
    profile: AthleteProfile,
    threshold: SportThreshold | None,
    observations: _StreamObservations,
    duration: int,
) -> _LoadEstimate:
    ftp = threshold.lt2_power_watts if threshold else None
    if observations.normalized_power is not None and ftp is not None and ftp > 0:
        method = (
            "athlete_threshold_power_stream" if observations.power else "athlete_threshold_power"
        )
        return _LoadEstimate(
            intensity_factor=round(observations.normalized_power / ftp, 2),
            tss=round(
                compute_tss(
                    duration,
                    sport=activity.sport,
                    normalized_power=observations.normalized_power,
                    ftp=ftp,
                ),
                1,
            ),
            method=method,
        )
    threshold_pace = threshold.lt2_pace_sec_per_km if threshold else None
    if activity.sport == "running" and observations.average_pace and threshold_pace:
        method = "athlete_threshold_pace_stream" if observations.pace else "athlete_threshold_pace"
        return _LoadEstimate(
            intensity_factor=round(threshold_pace / observations.average_pace, 2),
            tss=round(
                compute_tss(
                    duration,
                    sport=activity.sport,
                    avg_pace_sec_km=observations.average_pace,
                    threshold_pace_sec_km=threshold_pace,
                ),
                1,
            ),
            method=method,
        )
    if observations.average_heart_rate and profile.resting_hr_bpm and profile.max_hr_bpm:
        method = "athlete_heart_rate_stream" if observations.heart_rate else "athlete_heart_rate"
        return _LoadEstimate(
            tss=round(
                compute_tss(
                    duration,
                    sport=activity.sport,
                    avg_hr=observations.average_heart_rate,
                    resting_hr=profile.resting_hr_bpm,
                    max_hr=profile.max_hr_bpm,
                    biological_sex=profile.biological_sex or "not_specified",
                ),
                1,
            ),
            method=method,
        )
    return _LoadEstimate()


def estimate_strava_training_load(
    activity: Activity,
    *,
    profile: AthleteProfile,
    threshold: SportThreshold | None,
    streams: StravaStreams | None = None,
) -> Activity:
    """Calculate athlete-specific load, retaining derived metrics but not raw streams."""
    duration = activity.duration_seconds
    if duration is None or duration <= 0:
        return activity
    streams = streams or {}
    observations = _stream_observations(activity, streams, duration)
    load = _training_load_estimate(activity, profile, threshold, observations, duration)
    zone_distribution = _stream_zone_distribution(
        activity,
        profile,
        threshold,
        streams,
        observations,
    )
    raw_extraction = dict(activity.raw_extraction or {})
    if streams:
        raw_extraction["strava_stream_derivation"] = {
            "sample_count": max((len(values) for values in streams.values()), default=0),
            "streams_used": sorted(streams),
            "raw_samples_stored": False,
        }
    if load.method:
        raw_extraction["training_load_estimate"] = {"method": load.method}
    return activity.model_copy(
        update={
            "avg_hr_bpm": observations.average_heart_rate,
            "avg_power_watts": observations.average_power,
            "avg_pace_sec_per_km": observations.average_pace,
            "avg_cadence_rpm": observations.average_cadence,
            "normalized_power_watts": observations.normalized_power,
            "intensity_factor": load.intensity_factor,
            "tss": load.tss,
            "zone_distribution": zone_distribution,
            "raw_extraction": raw_extraction,
        }
    )


def map_strava_activity(user_id: str, athlete_id: int, item: dict[str, Any]) -> Activity | None:
    raw_id = item.get("id")
    if isinstance(raw_id, bool) or not isinstance(raw_id, int | str):
        return None
    activity_id = str(raw_id).strip()
    activity_date, started_at = _parse_activity_dates(item)
    if not activity_id or activity_date is None:
        return None

    duration_seconds = _first_positive_int(item.get("moving_time"), item.get("elapsed_time"))
    try:
        return Activity(
            user_id=user_id,
            sport=_map_strava_sport(item),
            activity_date=activity_date,
            started_at=started_at,
            duration_seconds=duration_seconds,
            distance_meters=_optional_float(item.get("distance")),
            elevation_gain_meters=_optional_float(item.get("total_elevation_gain")),
            avg_hr_bpm=_optional_int(item.get("average_heartrate")),
            max_hr_bpm=_optional_int(item.get("max_heartrate")),
            avg_power_watts=_optional_int(item.get("average_watts")),
            normalized_power_watts=_optional_int(item.get("weighted_average_watts")),
            avg_cadence_rpm=_optional_int(item.get("average_cadence")),
            # Athlete-specific load is estimated by the sync boundary after it
            # loads the coaching profile and active sport thresholds.
            source="strava_sync",
            source_file_key=f"strava:{athlete_id}:{activity_id}",
            raw_extraction={"strava_summary": _provenance(item)},
        )
    except (TypeError, ValueError):
        logger.warning("skipping malformed Strava activity id=%s", activity_id)
        return None


def _provenance(item: dict[str, Any]) -> dict[str, Any]:
    return {field: item[field] for field in _STRAVA_PROVENANCE_FIELDS if field in item}


def _map_strava_sport(item: dict[str, Any]) -> str:
    raw = item.get("sport_type") or item.get("type")
    normalized = str(raw or "").strip().casefold().replace("_", "").replace(" ", "")
    return _STRAVA_SPORT_MAP.get(normalized, "general")


def _parse_activity_dates(item: dict[str, Any]) -> tuple[date | None, datetime | None]:
    local_value = item.get("start_date_local")
    absolute_value = item.get("start_date")
    activity_date = _optional_date(local_value) or _optional_date(absolute_value)
    started_at = _optional_datetime(absolute_value)
    return activity_date, started_at
