import math
import os
from typing import Literal, Self

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Model families that support OpenAI's `reasoning` parameter on the Responses API.
# The vision service always passes Reasoning(effort=...) — a non-reasoning model will
# fail permanently at call time, so we reject the configuration at startup instead.
#
# When upgrading to a new reasoning model, add its name prefix here and confirm it
# supports the `reasoning` parameter before deploying. Each entry covers the named
# family and all newer variants (e.g. "o4" covers o4-mini, o4, o4-pro, etc.).
_REASONING_CAPABLE_MODEL_PREFIXES: frozenset[str] = frozenset(
    {
        "o1",  # o1, o1-mini, o1-preview — and newer o1 variants
        "o3",  # o3, o3-mini — and newer o3 variants
        "o4",  # o4-mini — and newer o4 variants
        "gpt-5",  # gpt-5, gpt-5-mini, gpt-5.4-mini — and newer gpt-5 variants
    }
)


class Settings(BaseSettings):
    app_env: str = "development"
    app_base_url: str = ""  # leave blank on Vercel preview; set explicitly for production
    app_jwt_secret: str = "replace-me"
    openai_api_key: str | None = None
    openai_activity_text_model: str = "gpt-5.5"
    openai_activity_text_timeout_seconds: float = 60.0
    openai_vision_model: str = "gpt-5.4-mini"
    openai_vision_timeout_seconds: float = 45.0
    # gpt-5.x vision is a reasoning model: reasoning draws down the output budget, so keep
    # this generous — a truncated response is invalid even under strict structured outputs.
    openai_vision_max_output_tokens: int = 8000
    # Keep reasoning light: screenshot extraction is a perception task, not a reasoning one.
    # Low effort leaves more of the token budget for output and reduces latency.
    openai_vision_reasoning_effort: Literal["minimal", "low", "medium", "high"] = "low"
    intervals_client_id: str = ""
    intervals_client_secret: str = ""
    intervals_token_encryption_secret: str = ""
    intervals_dev_api_key: str = ""
    intervals_dev_athlete_id: str = ""
    # Strava OAuth. Access tokens expire ~6h, so even local development uses the
    # refresh-token flow (there is deliberately no static dev token bypass).
    strava_integration_enabled: bool = False
    strava_client_id: str = ""
    strava_client_secret: str = ""
    strava_token_encryption_secret: str = ""
    # Coarse label for the authorization the athlete consented under. Surfaced in
    # status so a reviewer can confirm which consent version a connection carries.
    strava_authorization_version: str = ""

    @model_validator(mode="after")
    def validate_oauth_jwt_secret(self) -> Self:
        if self.strava_integration_enabled and self.app_jwt_secret.strip() == "replace-me":
            raise ValueError(
                "APP_JWT_SECRET must not use the placeholder value when Strava is enabled"
            )
        return self

    @field_validator("openai_activity_text_model")
    @classmethod
    def validate_openai_activity_text_model(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("OpenAI model names must not be empty or whitespace")
        return stripped

    @field_validator("openai_vision_model")
    @classmethod
    def validate_openai_vision_model(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("openai_vision_model must not be empty or whitespace")
        if not any(stripped.startswith(prefix) for prefix in _REASONING_CAPABLE_MODEL_PREFIXES):
            approved = ", ".join(sorted(f'"{p}*"' for p in _REASONING_CAPABLE_MODEL_PREFIXES))
            raise ValueError(
                f"openai_vision_model {stripped!r} is not an approved reasoning-capable model. "
                f"The vision service passes OpenAI reasoning tokens and requires a model from "
                f"an approved family: {approved}. "
                f"To use a new reasoning model, add its name prefix to "
                f"_REASONING_CAPABLE_MODEL_PREFIXES in backend/config.py."
            )
        return stripped

    @field_validator("openai_activity_text_timeout_seconds", "openai_vision_timeout_seconds")
    @classmethod
    def validate_openai_timeout(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0:
            raise ValueError("OpenAI timeouts must be finite numbers > 0")
        return v

    @field_validator("openai_vision_max_output_tokens")
    @classmethod
    def validate_vision_max_output_tokens(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("openai_vision_max_output_tokens must be a positive integer")
        return v

    @field_validator("openai_vision_reasoning_effort", mode="before")
    @classmethod
    def normalize_vision_reasoning_effort(cls, v: object) -> object:
        # Normalize so e.g. "Low" / " low " from the environment match the Literal.
        if isinstance(v, str):
            return v.strip().lower()
        return v

    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_bucket: str | None = None
    r2_endpoint_url: str | None = None
    r2_public_base_url: str | None = None
    r2_secret_access_key: str | None = None
    supabase_service_role_key: str | None = None
    supabase_url: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def is_vercel_deployment(self) -> bool:
        """Whether the process is running on a Vercel deployment."""
        return bool(os.environ.get("VERCEL_URL"))

    @property
    def base_url(self) -> str:
        """Effective base URL for OAuth, JWTs, and redirects.

        Priority: APP_BASE_URL env var → VERCEL_URL (auto-set by Vercel) → localhost.
        VERCEL_URL covers both preview and production deployments when APP_BASE_URL is unset.
        """
        if self.app_base_url:
            return self.app_base_url
        if self.is_vercel_deployment:
            return f"https://{os.environ['VERCEL_URL']}"
        return "http://localhost:3000"


settings = Settings()
