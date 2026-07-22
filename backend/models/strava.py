"""Pydantic models for the Strava OAuth and connection lifecycle.

Strava's initial token exchange and its refresh response are deliberately
modeled separately: the exchange returns the ``athlete`` object, but the refresh
response does not include the athlete (and does not reliably echo scope), so one
strict model cannot cover both.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

# Strava marks a token stale within an hour of expiry; refresh at that threshold.
STRAVA_REFRESH_THRESHOLD_SECONDS = 3600

# The activity read scopes; at least one must be granted for the connection to be useful.
STRAVA_ACTIVITY_READ_SCOPES = frozenset({"activity:read", "activity:read_all"})


def normalize_strava_scopes(raw: str) -> list[str]:
    """Split Strava's comma/space-delimited scope string into a clean list."""
    parts = raw.replace(",", " ").split()
    seen: list[str] = []
    for part in parts:
        scope = part.strip()
        if scope and scope not in seen:
            seen.append(scope)
    return seen


def has_required_activity_scope(scopes: list[str]) -> bool:
    return any(scope in STRAVA_ACTIVITY_READ_SCOPES for scope in scopes)


class StravaAuthorizationResponse(BaseModel):
    redirect_url: str


class StravaOAuthState(BaseModel):
    user_id: str


class StravaAthlete(BaseModel):
    id: int
    firstname: str | None = None
    lastname: str | None = None
    username: str | None = None

    @field_validator("id")
    @classmethod
    def positive_id(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Strava athlete id must be a positive integer")
        return value

    @property
    def display_name(self) -> str | None:
        full = " ".join(part for part in (self.firstname, self.lastname) if part).strip()
        return full or self.username or None


class StravaTokenResponse(BaseModel):
    """Response body from the authorization-code exchange (includes athlete)."""

    access_token: str
    refresh_token: str
    expires_at: int
    token_type: str = "Bearer"
    athlete: StravaAthlete

    @field_validator("access_token", "refresh_token", "token_type")
    @classmethod
    def non_empty_string(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Strava token fields must not be empty")
        return stripped

    @field_validator("expires_at")
    @classmethod
    def positive_epoch(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Strava expires_at must be a positive epoch value")
        return value

    @property
    def expires_at_datetime(self) -> datetime:
        return datetime.fromtimestamp(self.expires_at, tz=UTC)


class StravaRefreshResponse(BaseModel):
    """Response body from a refresh-token grant (no athlete, no reliable scope)."""

    access_token: str
    refresh_token: str
    expires_at: int
    token_type: str = "Bearer"

    @field_validator("access_token", "refresh_token", "token_type")
    @classmethod
    def non_empty_string(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Strava token fields must not be empty")
        return stripped

    @field_validator("expires_at")
    @classmethod
    def positive_epoch(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Strava expires_at must be a positive epoch value")
        return value

    @property
    def expires_at_datetime(self) -> datetime:
        return datetime.fromtimestamp(self.expires_at, tz=UTC)


class StravaConnectionCreate(BaseModel):
    user_id: str
    strava_athlete_id: int
    strava_athlete_name: str | None = None
    scopes: list[str] = Field(default_factory=list)
    access_token_ciphertext: str
    refresh_token_ciphertext: str
    token_type: str = "Bearer"
    expires_at: datetime
    authorization_version: str | None = None


class StravaConnectionRecord(BaseModel):
    id: str
    user_id: str
    strava_athlete_id: int
    strava_athlete_name: str | None = None
    scopes: list[str] = Field(default_factory=list)
    access_token_ciphertext: str
    refresh_token_ciphertext: str
    token_type: str = "Bearer"
    expires_at: datetime
    authorization_version: str | None = None
    consented_at: datetime | None = None
    connected_at: datetime
    updated_at: datetime
    last_sync_at: datetime | None = None
    revoked_at: datetime | None = None


class StravaTokenRotation(BaseModel):
    """New token material to persist after a successful refresh."""

    access_token_ciphertext: str
    refresh_token_ciphertext: str
    token_type: str = "Bearer"
    expires_at: datetime


class StravaConnectionStatus(BaseModel):
    """Secret-free view of a connection for the browser."""

    connected: bool
    disconnect_pending: bool = False
    connected_at: datetime | None = None
    last_sync_at: datetime | None = None
    strava_athlete_id: int | None = None
    strava_athlete_name: str | None = None
    scopes: list[str] = Field(default_factory=list)
    authorization_version: str | None = None
