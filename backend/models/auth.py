from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field


class UserContext(BaseModel):
    client_id: str | None = None
    grant_id: str | None = None
    scopes: list[str] = Field(default_factory=list)
    user_id: str


class OAuthAuthorizeRequest(BaseModel):
    client_id: str
    code_challenge: str | None = None
    code_challenge_method: str | None = None
    redirect_uri: str
    response_type: str = "code"
    scope: str = "profile:read plans:write metrics:write"
    state: str | None = None


class OAuthTokenRequest(BaseModel):
    client_id: str
    code: str
    code_verifier: str | None = None
    grant_type: str = "authorization_code"
    redirect_uri: str


class TokenBundle(BaseModel):
    access_token: str
    expires_at: datetime = Field(default_factory=lambda: datetime.now(UTC) + timedelta(minutes=15))
    refresh_token: str
    token_type: str = "Bearer"
