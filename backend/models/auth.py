from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field


class BrowserSessionRequest(BaseModel):
    access_token: str


class BrowserSessionContext(BaseModel):
    email: str | None = None
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
    expires_at: datetime = Field(default_factory=lambda: datetime.now(UTC) + timedelta(minutes=15))
    refresh_token: str
    token_type: str = "Bearer"
