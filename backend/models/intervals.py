from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class IntervalsAuthorizationResponse(BaseModel):
    redirect_url: str


class IntervalsOAuthState(BaseModel):
    user_id: str


class IntervalsAthlete(BaseModel):
    id: str
    name: str | None = None

    @field_validator("id")
    @classmethod
    def non_empty_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Intervals athlete id must not be empty")
        return stripped


class IntervalsTokenResponse(BaseModel):
    access_token: str
    athlete: IntervalsAthlete
    scope: str = ""
    token_type: str = "Bearer"

    @field_validator("access_token", "token_type")
    @classmethod
    def non_empty_string(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Intervals token fields must not be empty")
        return stripped

    @property
    def scopes(self) -> list[str]:
        return [scope.strip() for scope in self.scope.split(",") if scope.strip()]


class IntervalsConnectionRecord(BaseModel):
    access_token_ciphertext: str
    connected_at: datetime
    id: str
    intervals_athlete_id: str
    intervals_athlete_name: str | None = None
    revoked_at: datetime | None = None
    scopes: list[str] = Field(default_factory=list)
    token_type: str = "Bearer"
    updated_at: datetime
    user_id: str


class IntervalsConnectionCreate(BaseModel):
    access_token_ciphertext: str
    intervals_athlete_id: str
    intervals_athlete_name: str | None = None
    scopes: list[str] = Field(default_factory=list)
    token_type: str = "Bearer"
    user_id: str


class IntervalsConnectionStatus(BaseModel):
    connected: bool
    connected_at: datetime | None = None
    intervals_athlete_id: str | None = None
    intervals_athlete_name: str | None = None
    scopes: list[str] = Field(default_factory=list)
