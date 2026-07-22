from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol, cast
from urllib.parse import urlencode

import httpx
import jwt
from pydantic import ValidationError

from backend.config import settings
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
STRAVA_DEAUTHORIZE_URL = "https://www.strava.com/oauth/deauthorize"
STRAVA_API_BASE = "https://www.strava.com/api/v3"
# Least-privileged read scope. `activity:read` covers everything but Only-Me
# activities; escalate to `activity:read_all` only under explicit approval.
STRAVA_DEFAULT_SCOPE = "read,activity:read"
STRAVA_STATE_TYPE = "strava_oauth_state"

# Bound a manual sync so a single request can never walk the athlete's entire
# history and exhaust the rate-limit budget.
STRAVA_SYNC_MAX_DAYS = 90
STRAVA_SYNC_PER_PAGE = 100
STRAVA_SYNC_MAX_PAGES = 10

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
        connection = self._repository.replace_connection(
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
            )
        )
        logger.info("strava connection stored scopes=%s", scopes)
        return self._status_from_record(connection)

    # ── Status / auth resolution ─────────────────────────────────

    def get_status(self, user_id: str) -> StravaConnectionStatus:
        self._require_enabled()
        return self._status_from_record(self._repository.get_active_connection(user_id))

    def record_sync(self, user_id: str) -> None:
        """Best-effort stamp of the last successful sync on the active connection."""
        self._repository.touch_last_sync(user_id)

    async def resolve_auth(self, user_id: str) -> StravaAuthContext:
        self._require_enabled()
        connection = self._repository.get_active_connection(user_id)
        if connection is None:
            raise StravaNotConnectedError("Strava is not connected.")
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
        rotated = self._repository.rotate_tokens(
            connection_id=connection.id,
            expected_expires_at=connection.expires_at,
            rotation=rotation,
        )
        if rotated is not None:
            return refreshed.access_token, rotated

        # A concurrent refresh already rotated the token; reload and use theirs so
        # we never race a stale-token write against a newer one.
        reloaded = self._repository.get_active_connection(connection.user_id)
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

    # ── Disconnect / revocation ──────────────────────────────────

    async def disconnect(self, user_id: str) -> StravaDisconnectResult:
        self._require_enabled()
        connection = self._repository.get_active_connection(user_id)
        if connection is None:
            return StravaDisconnectResult(
                status=StravaConnectionStatus(connected=False), remote_revoked=True
            )

        access_token = self._cipher().decrypt(connection.access_token_ciphertext)
        try:
            await self._deauthorize(access_token)
        except StravaSyncError:
            # Retryable upstream failure: keep credentials so a retry can revoke,
            # but block reads by leaving the connection marked pending.
            logger.warning("strava remote revocation deferred user_id=%s", user_id)
            return StravaDisconnectResult(
                status=self._status_from_record(connection, disconnect_pending=True),
                remote_revoked=False,
            )

        self._repository.revoke_active_connection(user_id)
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

    async def _deauthorize(self, access_token: str) -> None:
        async with self._http_client_factory() as client:
            try:
                response = await client.post(
                    STRAVA_DEAUTHORIZE_URL, data={"access_token": access_token}
                )
            except httpx.HTTPError as exc:
                raise StravaSyncError("Strava revocation failed.") from exc
            # 401 means the grant is already gone at Strava — treat as success.
            if response.status_code in (httpx.codes.OK, httpx.codes.UNAUTHORIZED):
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
            # TSS/IF/zones require athlete thresholds Strava does not provide;
            # leave unset rather than fabricate them.
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
