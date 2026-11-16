from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

ACCESS_TOKEN_TTL = timedelta(minutes=15)

# RFC 8628 §6.1 user codes: 8 consonants (no vowels, so no words are spelled; no digits, so
# 0/O and 1/I cannot be confused), about 34 bits of entropy. Displayed as ``XXXX-XXXX``.
DEVICE_USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"
DEVICE_USER_CODE_LENGTH = 8


class BrowserSessionRequest(BaseModel):
    access_token: str


class BrowserSessionContext(BaseModel):
    email: str | None = None
    user_id: str


class BrowserTokenResponse(BaseModel):
    access_token: str
    expires_at: datetime = Field(default_factory=lambda: datetime.now(UTC) + ACCESS_TOKEN_TTL)
    scopes: list[str] = Field(default_factory=list)
    token_type: str = "Bearer"
    user_id: str


class UserContext(BaseModel):
    client_id: str | None = None
    grant_id: str | None = None
    scopes: list[str] = Field(default_factory=list)
    user_id: str


class OAuthAuthorizeRequest(BaseModel):
    client_id: str
    code_challenge: str | None = None
    code_challenge_method: str | None = None
    prompt: str | None = None
    redirect_uri: str
    response_type: str = "code"
    scope: str = "profile:read plans:write metrics:write"
    state: str | None = None


class OAuthTokenRequest(BaseModel):
    client_id: str
    code: str | None = None
    code_verifier: str | None = None
    grant_type: str = "authorization_code"
    redirect_uri: str | None = None
    refresh_token: str | None = None
    # RFC 8628 §3.4: only for ``urn:ietf:params:oauth:grant-type:device_code``.
    device_code: str | None = None


class OAuthRevokeRequest(BaseModel):
    client_id: str | None = None
    token: str
    token_type_hint: str | None = None


class OAuthGrantRecord(BaseModel):
    client_id: str
    created_at: datetime
    id: str
    redirect_uri: str
    revoked_at: datetime | None = None
    scopes: list[str] = Field(default_factory=list)
    updated_at: datetime
    user_id: str


class OAuthAuthorizationCodeRecord(BaseModel):
    client_id: str
    code_challenge: str | None = None
    code_challenge_method: str | None = None
    consumed_at: datetime | None = None
    created_at: datetime
    expires_at: datetime
    grant_id: str
    id: str
    redirect_uri: str
    scopes: list[str] = Field(default_factory=list)
    user_id: str


class OAuthRefreshTokenRecord(BaseModel):
    client_id: str
    created_at: datetime
    expires_at: datetime
    grant_id: str
    id: str
    revoked_at: datetime | None = None
    rotated_from_id: str | None = None
    scopes: list[str] = Field(default_factory=list)
    user_id: str


DeviceCodeStatus = Literal["pending", "approved", "denied", "consumed"]


class OAuthDeviceCodeRecord(BaseModel):
    approved_at: datetime | None = None
    client_id: str
    created_at: datetime
    expires_at: datetime
    grant_id: str | None = None
    id: str
    interval_seconds: int
    last_polled_at: datetime | None = None
    scopes: list[str] = Field(default_factory=list)
    status: DeviceCodeStatus
    user_code: str
    user_id: str | None = None


class TokenBundle(BaseModel):
    access_token: str
    expires_at: datetime = Field(default_factory=lambda: datetime.now(UTC) + ACCESS_TOKEN_TTL)
    # RFC 6749 §5.1 fields, alongside the legacy absolute ``expires_at``.
    expires_in: int = int(ACCESS_TOKEN_TTL.total_seconds())
    refresh_token: str
    scope: str = ""
    token_type: str = "Bearer"


class CliTokenResponse(TokenBundle):
    user_id: str


class WhoAmIResponse(BaseModel):
    client_id: str | None = None
    scopes: list[str] = Field(default_factory=list)
    user_id: str


class DeviceAuthorizationRequest(BaseModel):
    """RFC 8628 §3.1 device authorization request (JSON body)."""

    client_id: str
    scope: str | None = None


class DeviceAuthorizationResponse(BaseModel):
    """RFC 8628 §3.2 device authorization response."""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int
