import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_base_url: str = ""  # leave blank on Vercel preview; set explicitly for production
    app_jwt_secret: str = "replace-me"
    openai_api_key: str | None = None
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
    def base_url(self) -> str:
        """Effective base URL for OAuth, JWTs, and redirects.

        Priority: APP_BASE_URL env var → VERCEL_URL (auto-set by Vercel) → localhost.
        VERCEL_URL covers both preview and production deployments when APP_BASE_URL is unset.
        """
        if self.app_base_url:
            return self.app_base_url
        vercel_url = os.environ.get("VERCEL_URL", "")
        if vercel_url:
            return f"https://{vercel_url}"
        return "http://localhost:3000"


settings = Settings()
