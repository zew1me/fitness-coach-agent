import re
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field, field_validator

ACCESS_TOKEN_TTL = timedelta(minutes=15)

# GoTrue's email OTP length is configurable from 6 to 10 digits.
_CLI_OTP_PATTERN = re.compile(r"[0-9]{6,10}")  # ASCII only: ``\d`` matches any Unicode digit
# Commonly cited upper bound for an email address (64-octet local part + 255-octet domain).
_MAX_EMAIL_LENGTH = 320


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


class TokenBundle(BaseModel):
    access_token: str
    expires_at: datetime = Field(default_factory=lambda: datetime.now(UTC) + ACCESS_TOKEN_TTL)
    # RFC 6749 §5.1 fields, alongside the legacy absolute ``expires_at``.
    expires_in: int = int(ACCESS_TOKEN_TTL.total_seconds())
    refresh_token: str
    scope: str = ""
    token_type: str = "Bearer"


class CliVerifyOtpRequest(BaseModel):
    """Email OTP submitted by the first-party CLI for server-side verification."""

    email: str = Field(max_length=_MAX_EMAIL_LENGTH)
    token: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        # Mirror app/api/auth/request-otp: the code was issued to the trimmed, lowercased address.
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("email must not be empty")
        return normalized

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: str) -> str:
        stripped = value.strip()
        if not _CLI_OTP_PATTERN.fullmatch(stripped):
            raise ValueError("token must be a 6-10 digit code")
        return stripped


class CliTokenResponse(TokenBundle):
    user_id: str


class WhoAmIResponse(BaseModel):
    client_id: str | None = None
    scopes: list[str] = Field(default_factory=list)
    user_id: str
